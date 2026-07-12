"""
Phase 4 / 4.8 — bidirectional page-fit: trim when over 1 page, expand when thin.
"""

from __future__ import annotations

import re
import warnings
from pathlib import Path
from typing import Any, Callable

from src.generation.generator import load_norms, norms_entry_for_intake
from src.generation.prompts import (
    pagefit_expand_instruction,
    pagefit_trim_instruction,
    resolve_role_profile,
)
from src.generation.renderer import count_pdf_pages, measure_page_fill, render_and_compile
from src.schemas import (
    MAX_BULLETS_PER_EXPERIENCE,
    MAX_BULLETS_PER_PROJECT,
    BULLET_MAX_LEN,
    BULLET_MIN_LEN,
    GenerationResult,
    Intake,
    ResumeContent,
    Suggestion,
)

MAX_TRIM_ATTEMPTS = 3
MAX_EXPAND_ATTEMPTS = 3
DEFAULT_FILL_TARGET = 0.88
DEFAULT_NORMS = Path("data/norms/norms.json")

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


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
            return ResumeContent.model_construct(**data)

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

    for e in experience:
        e["bullets"] = (e.get("bullets") or [])[: max(1, MAX_BULLETS_PER_EXPERIENCE - 1)]
    for p in projects:
        p["bullets"] = (p.get("bullets") or [])[: max(1, MAX_BULLETS_PER_PROJECT - 1)]
    return _pack()


def _normalize_fact(text: str) -> str:
    t = (text or "").lower()
    t = re.sub(r"[^\w\s+#./%-]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _split_facts(text: str) -> list[str]:
    parts = _SENTENCE_SPLIT.split((text or "").strip())
    out: list[str] = []
    for p in parts:
        p = p.strip(" -\n\t")
        if len(p) < 20:
            continue
        out.append(p)
    return out


def attested_rewritten_from(result: GenerationResult) -> list[str]:
    """Collect rewritten_from strings if present on annotated path — else bullet texts."""
    # GenerationResult only has final ResumeContent; use bullet texts as attestation proxy.
    attested: list[str] = []
    for e in result.resume.experience or []:
        for b in e.get("bullets") or []:
            attested.append(str(b))
    for p in result.resume.projects or []:
        for b in p.get("bullets") or []:
            attested.append(str(b))
    return attested


def unused_intake_facts(
    intake: Intake,
    result: GenerationResult,
    *,
    qa_answers: list[str] | None = None,
    qa_entries: list[dict[str, str]] | None = None,
) -> list[str]:
    """
    Intake description sentences + answered QA facts that are not covered by
    any generated bullet text (token overlap heuristic). G1-safe expansion fuel.

    `qa_entries` is preferred: each item is ``{"answer": ..., "relates_to": ...}``.
    Answered QA uses a stricter overlap threshold (0.75) so partially-related
    bullets do not swallow a distinct elicited fact. Description sentences keep
    the looser 0.45 threshold.
    """
    attested_norm = [_normalize_fact(a) for a in attested_rewritten_from(result)]

    def _covered(fact: str, *, threshold: float = 0.45) -> bool:
        fn = _normalize_fact(fact)
        if len(fn) < 12:
            return True
        fwords = {w for w in fn.split() if len(w) > 3}
        if not fwords:
            return True
        for a in attested_norm:
            awords = {w for w in a.split() if len(w) > 3}
            if not awords:
                continue
            overlap = len(fwords & awords) / max(1, len(fwords))
            # Prefix match only counts as covered for the looser description path
            if overlap >= threshold:
                return True
            if threshold <= 0.45 and fn[:40] in a:
                return True
        return False

    unused: list[str] = []
    for exp in intake.experience:
        for sent in _split_facts(exp.description):
            if not _covered(sent, threshold=0.45):
                unused.append(f"[{exp.company}] {sent}")
    for proj in intake.projects:
        for sent in _split_facts(proj.description):
            if not _covered(sent, threshold=0.45):
                unused.append(f"[{proj.name}] {sent}")

    entries = list(qa_entries or [])
    if not entries and qa_answers:
        entries = [{"answer": a, "relates_to": ""} for a in qa_answers if a]

    for entry in entries:
        ans = (entry.get("answer") or "").strip()
        if not ans:
            continue
        relates = (entry.get("relates_to") or "").strip()
        # Prefer labeling with relates_to so the expand prompt targets the right entry
        label = relates if relates else "answer"
        # Stricter threshold: elicited answers exist because they filled a named gap
        if not _covered(ans, threshold=0.75):
            unused.append(f"[{label}] {ans}")
    return unused


def thin_entries(
    content: ResumeContent,
    *,
    target_per_entry: float = 3.0,
) -> list[str]:
    """Entries with bullet count below the corpus upper-band target."""
    target = max(2, int(round(target_per_entry)))
    out: list[str] = []
    for e in content.experience or []:
        n = len(e.get("bullets") or [])
        if n < target:
            out.append(
                f"experience `{e.get('company', '?')}`: {n} bullets "
                f"(target ≥{target})"
            )
    for p in content.projects or []:
        n = len(p.get("bullets") or [])
        # projects often slightly thinner
        pt = max(2, target - 1)
        if n < pt:
            out.append(
                f"project `{p.get('name', '?')}`: {n} bullets (target ≥{pt})"
            )
    return out


def fit_to_one_page(
    intake: Intake,
    initial: GenerationResult,
    out_dir: Path,
    *,
    generate_fn: Callable[..., GenerationResult],
    basename: str = "resume",
    max_attempts: int = MAX_TRIM_ATTEMPTS,
    fill_target: float = DEFAULT_FILL_TARGET,
    max_expand_attempts: int = MAX_EXPAND_ATTEMPTS,
    norms_path: Path = DEFAULT_NORMS,
    qa_answers: list[str] | None = None,
    qa_entries: list[dict[str, str]] | None = None,
) -> tuple[GenerationResult, Path, Path, int, dict[str, Any]]:
    """
    Trim if >1 page; if 1 page but under-filled, expand using unused intake facts.

    Returns (result, tex_path, pdf_path, page_count, fill_meta).
    """
    role = resolve_role_profile(intake.target_role)
    norms = load_norms(norms_path)
    norms_entry, _, _ = norms_entry_for_intake(norms, intake)
    p75 = float(norms_entry.get("bullets_per_entry_p75") or 3.0)

    result = initial
    tex_path, pdf_path = render_and_compile(result.resume, out_dir, basename=basename)
    pages = count_pdf_pages(pdf_path)
    fill = measure_page_fill(pdf_path) if pages == 1 else 0.0

    best: tuple[GenerationResult, Path, Path, int, float] = (
        result,
        tex_path,
        pdf_path,
        pages,
        fill if pages == 1 else -1.0,
    )
    expand_attempts = 0

    # --- Trim loop ---
    if pages > 1:
        for attempt in range(1, max_attempts + 1):
            excess = pages - 1
            cut = max(2, min(6, excess * 3 + attempt))
            trim = pagefit_trim_instruction(
                pages=pages,
                cut_lines=cut,
                bullet_count=total_bullets(result.resume),
                role=role,
            )
            result = generate_fn(intake, trim_instruction=trim)
            tex_path, pdf_path = render_and_compile(
                result.resume, out_dir, basename=basename
            )
            pages = count_pdf_pages(pdf_path)
            fill = measure_page_fill(pdf_path) if pages == 1 else 0.0
            if pages == 1 and fill >= best[4]:
                best = (result, tex_path, pdf_path, pages, fill)
            if pages <= 1:
                break
        else:
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
                tex_path, pdf_path = render_and_compile(
                    content, out_dir, basename=basename
                )
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
                    result = GenerationResult(resume=content, suggestions=suggestions)
                    fill = measure_page_fill(pdf_path)
                    best = (result, tex_path, pdf_path, pages, fill)
                    break
            else:
                suggestions.append(
                    Suggestion(
                        type="content_gap",
                        detail=(
                            f"Could not fit to one page (still {pages} pages after hard-trim)."
                        ),
                    )
                )
                result = GenerationResult(resume=content, suggestions=suggestions)
                meta = {
                    "fill_ratio": 0.0,
                    "fill_target": fill_target,
                    "expand_attempts": 0,
                    "needs_expansion_elicit": False,
                    "thin_entries": [],
                    "unused_facts": [],
                }
                return result, tex_path, pdf_path, pages, meta

    result, tex_path, pdf_path, pages, fill = best
    if pages != 1:
        meta = {
            "fill_ratio": fill if fill > 0 else 0.0,
            "fill_target": fill_target,
            "expand_attempts": 0,
            "needs_expansion_elicit": False,
            "thin_entries": thin_entries(result.resume, target_per_entry=p75),
            "unused_facts": [],
        }
        return result, tex_path, pdf_path, pages, meta

    # --- Expand loop ---
    while fill < fill_target and expand_attempts < max_expand_attempts:
        unused = unused_intake_facts(
            intake, result, qa_answers=qa_answers, qa_entries=qa_entries
        )
        thin = thin_entries(result.resume, target_per_entry=p75)
        if not unused and not thin:
            break
        expand_attempts += 1
        # Stronger on later attempts: demand unused facts be turned into bullets
        must_use = ""
        if unused:
            must_use = (
                f"\nMUST incorporate at least "
                f"{min(3, len(unused))} of the unused facts below as NEW bullets "
                f"(each {BULLET_MIN_LEN}–{BULLET_MAX_LEN} chars). Prefer Homebrew/deploy/scale/security facts "
                f"that are still missing. Lengthen short facts with tools/scope from "
                f"the same sentence — do not invent new numbers.\n"
                f"When a fact is labeled with a company/project name, attach the new "
                f"bullet to that entry if it still has bullet budget remaining.\n"
            )
        instr = pagefit_expand_instruction(
            fill_ratio=fill,
            fill_target=fill_target,
            bullet_count=total_bullets(result.resume),
            thin_entries=thin
            or [f"(density ok by count, but page only {fill:.0%} full — add unused facts)"],
            unused_facts=unused,
            role=role,
            bullets_per_entry_p75=p75,
        ) + must_use
        try:
            candidate = generate_fn(intake, expand_instruction=instr)
            c_tex, c_pdf = render_and_compile(
                candidate.resume, out_dir, basename=basename
            )
        except Exception as e:
            warnings.warn(
                f"Page-fill expand attempt {expand_attempts} failed ({e}); "
                "keeping best one-page draft.",
                UserWarning,
                stacklevel=2,
            )
            continue
        c_pages = count_pdf_pages(c_pdf)
        if c_pages > 1:
            # Overflow — keep best one-page draft
            continue
        c_fill = measure_page_fill(c_pdf)
        if c_fill >= fill:
            result, tex_path, pdf_path, pages, fill = (
                candidate,
                c_tex,
                c_pdf,
                c_pages,
                c_fill,
            )
            best = (result, tex_path, pdf_path, pages, fill)

    result, tex_path, pdf_path, pages, fill = best
    # Re-render the best draft — expand attempts share the same basename and may
    # have overwritten tex/pdf on disk with a rejected (overflow / worse) candidate.
    if pages == 1:
        tex_path, pdf_path = render_and_compile(
            result.resume, out_dir, basename=basename
        )
        pages = count_pdf_pages(pdf_path)
        fill = measure_page_fill(pdf_path) if pages == 1 else fill

    unused_final = unused_intake_facts(
        intake, result, qa_answers=qa_answers, qa_entries=qa_entries
    )
    thin_final = thin_entries(result.resume, target_per_entry=p75)
    # Under target → elicit more content even if entry bullet counts look fine
    needs_elicit = pages == 1 and fill < fill_target

    meta = {
        "fill_ratio": round(fill, 4),
        "fill_target": fill_target,
        "expand_attempts": expand_attempts,
        "needs_expansion_elicit": needs_elicit,
        "thin_entries": thin_final,
        "unused_facts": unused_final[:12],
        "bullets_per_entry_p75": p75,
    }
    return result, tex_path, pdf_path, pages, meta


# Back-compat alias used by older imports
fit_page = fit_to_one_page
