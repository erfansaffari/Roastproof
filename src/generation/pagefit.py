"""
Phase 4 — page-fit loop.

Compile → count pages. If >1: re-generate with a trim instruction (max 3
attempts), then hard-trim deterministically and warn.
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Callable

from src.generation.renderer import count_pdf_pages, render_and_compile
from src.schemas import (
    MAX_BULLETS_PER_EXPERIENCE,
    MAX_BULLETS_PER_PROJECT,
    GenerationResult,
    Intake,
    ResumeContent,
    Suggestion,
)

MAX_TRIM_ATTEMPTS = 3


def total_bullets(content: ResumeContent) -> int:
    n = sum(len(e.get("bullets") or []) for e in content.experience)
    n += sum(len(p.get("bullets") or []) for p in content.projects)
    return n


def hard_trim(content: ResumeContent) -> ResumeContent:
    """
    Deterministic shrink: drop last project bullets, then last experience
    bullets, then drop trailing projects — until likely one page.
    """
    data = content.model_dump()
    projects = data.get("projects") or []
    experience = data.get("experience") or []

    def _pack() -> ResumeContent:
        data["projects"] = projects
        data["experience"] = experience
        try:
            return ResumeContent.model_validate(data)
        except Exception:
            # Intermediate states may still exceed the total-bullet budget.
            return ResumeContent.model_construct(**data)

    # Drop trailing bullets from last project, then earlier projects.
    for i in range(len(projects) - 1, -1, -1):
        bullets = list(projects[i].get("bullets") or [])
        while len(bullets) > 1:
            bullets.pop()
            projects[i]["bullets"] = bullets
            return _pack()
        if len(projects) > 1:
            projects.pop()
            return _pack()

    for i in range(len(experience) - 1, -1, -1):
        bullets = list(experience[i].get("bullets") or [])
        while len(bullets) > 1:
            bullets.pop()
            experience[i]["bullets"] = bullets
            return _pack()

    # Last resort: cap bullet counts.
    for e in experience:
        e["bullets"] = (e.get("bullets") or [])[: max(1, MAX_BULLETS_PER_EXPERIENCE - 1)]
    for p in projects:
        p["bullets"] = (p.get("bullets") or [])[: max(1, MAX_BULLETS_PER_PROJECT - 1)]
    return _pack()


def fit_to_one_page(
    intake: Intake,
    initial: GenerationResult,
    out_dir: Path,
    *,
    generate_fn: Callable[..., GenerationResult],
    basename: str = "resume",
    max_attempts: int = MAX_TRIM_ATTEMPTS,
) -> tuple[GenerationResult, Path, Path, int]:
    """
    Render/compile until ≤1 page or attempts exhausted.

    Returns (result, tex_path, pdf_path, page_count).
    """
    result = initial
    tex_path, pdf_path = render_and_compile(result.resume, out_dir, basename=basename)
    pages = count_pdf_pages(pdf_path)
    if pages <= 1:
        return result, tex_path, pdf_path, pages

    for attempt in range(1, max_attempts + 1):
        excess = pages - 1
        # Rough heuristic: ~3–4 bullets per extra page-ish; ask to cut a few.
        cut = max(2, min(6, excess * 3 + attempt))
        from src.generation.prompts import pagefit_trim_instruction, resolve_role_profile

        trim = pagefit_trim_instruction(
            pages=pages,
            cut_lines=cut,
            bullet_count=total_bullets(result.resume),
            role=resolve_role_profile(intake.target_role),
        )
        result = generate_fn(intake, trim_instruction=trim)
        tex_path, pdf_path = render_and_compile(result.resume, out_dir, basename=basename)
        pages = count_pdf_pages(pdf_path)
        if pages <= 1:
            return result, tex_path, pdf_path, pages

    # Hard trim loop (deterministic).
    warnings.warn(
        f"Page-fit: still {pages} pages after {max_attempts} LLM trim attempts; "
        "applying deterministic hard-trim.",
        UserWarning,
        stacklevel=2,
    )
    content = result.resume
    suggestions = list(result.suggestions)
    for _ in range(12):
        content = hard_trim(content)
        tex_path, pdf_path = render_and_compile(content, out_dir, basename=basename)
        pages = count_pdf_pages(pdf_path)
        if pages <= 1:
            suggestions.append(
                Suggestion(
                    type="content_gap",
                    detail=(
                        "Resume was hard-trimmed to fit one page after LLM trim "
                        "attempts failed. Review dropped bullets and restore only "
                        "if you can free space elsewhere."
                    ),
                )
            )
            return (
                GenerationResult(resume=content, suggestions=suggestions),
                tex_path,
                pdf_path,
                pages,
            )

    suggestions.append(
        Suggestion(
            type="content_gap",
            detail=f"Could not fit to one page (still {pages} pages after hard-trim).",
        )
    )
    return (
        GenerationResult(resume=content, suggestions=suggestions),
        tex_path,
        pdf_path,
        pages,
    )
