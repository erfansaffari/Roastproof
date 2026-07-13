"""Unit tests for Phase 4 / 4.5 generation helpers (no live LLM required)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.generation.fluff import find_fluff_hits, lint_resume_fluff
from src.generation.generator import (
    allowed_skills,
    annotated_to_resume,
    enforce_g1,
    expand_skill_tokens,
    format_norms_block,
    intake_norms_bucket,
    skill_is_owned,
)
from src.generation.intake import load_intake
from src.generation.pagefit import hard_trim, total_bullets
from src.generation.renderer import latex_escape, render_tex
from src.schemas import (
    AnnotatedBullet,
    AnnotatedExperience,
    AnnotatedGenerationResult,
    AnnotatedProject,
    AnnotatedResume,
    Intake,
    ResumeContent,
    Suggestion,
)


def _long(s: str, n: int = 70) -> str:
    """Pad a bullet to satisfy ResumeContent length validators."""
    if len(s) >= n:
        return s[:140]
    return (s + " " + ("x" * (n - len(s) - 1))).strip()


def _annotated(
    *,
    skills: dict | None = None,
    experience_bullets: list[AnnotatedBullet] | None = None,
    suggestions: list[Suggestion] | None = None,
) -> AnnotatedGenerationResult:
    bullets = experience_bullets or [
        AnnotatedBullet(
            text=_long("Built internal tooling in Python for equipment checkout workflows"),
            rewritten_from="Built internal tooling",
            gaps=[],
        )
    ]
    return AnnotatedGenerationResult(
        resume=AnnotatedResume(
            contact={
                "name": "Alex Chen",
                "email": "a@b.com",
                "phone": "555",
                "linkedin": "",
                "github": "",
                "website": "",
            },
            education=[
                {
                    "school": "State U",
                    "degree": "BS CS",
                    "dates": "2023--Present",
                    "location": "TX",
                    "details": "",
                }
            ],
            experience=[
                AnnotatedExperience(
                    company="Lab",
                    title="Intern",
                    dates="2025",
                    location="TX",
                    bullets=bullets,
                )
            ],
            projects=[
                AnnotatedProject(
                    name="Planner",
                    technologies="React, TypeScript",
                    dates="2025",
                    bullets=[
                        AnnotatedBullet(
                            text=_long(
                                "Built a degree planner web app used by classmates during advising"
                            ),
                            rewritten_from="degree planner",
                            gaps=[],
                        )
                    ],
                )
            ],
            skills=skills
            or {
                "Languages": ["Python", "TypeScript"],
                "Frameworks": ["React"],
                "Developer Tools": ["Docker"],
            },
            section_order=["education", "experience", "projects", "skills"],
        ),
        suggestions=suggestions or [],
    )


def _minimal_resume(**overrides) -> ResumeContent:
    base = {
        "contact": {
            "name": "Alex Chen",
            "email": "a@b.com",
            "phone": "555",
            "linkedin": "",
            "github": "",
            "website": "",
        },
        "education": [
            {
                "school": "State U",
                "degree": "BS CS",
                "dates": "2023--Present",
                "location": "TX",
                "details": "",
            }
        ],
        "experience": [
            {
                "company": "Lab",
                "title": "Intern",
                "dates": "2025",
                "location": "TX",
                "bullets": [
                    _long("Built internal tooling in Python for equipment checkout workflows"),
                ],
            }
        ],
        "projects": [
            {
                "name": "Planner",
                "technologies": "React, TypeScript",
                "dates": "2025",
                "bullets": [
                    _long("Built a degree planner web app used by classmates during advising"),
                ],
            }
        ],
        "skills": {
            "Languages": ["Python", "TypeScript"],
            "Frameworks": ["React"],
            "Developer Tools": ["Docker"],
        },
        "section_order": ["education", "experience", "projects", "skills"],
    }
    base.update(overrides)
    return ResumeContent.model_validate(base)


def test_latex_escape_adversarial():
    raw = "C# & F_measure 100%"
    escaped = latex_escape(raw)
    assert r"\&" in escaped
    assert r"\_" in escaped
    assert r"\%" in escaped
    assert "#" in escaped or r"\#" in escaped
    all_specials = r"\ % & # _ $ { } ~ ^"
    out = latex_escape(all_specials)
    assert r"\textbackslash{}" in out
    assert r"\%" in out
    assert r"\&" in out
    assert r"\#" in out
    assert r"\_" in out
    assert r"\$" in out
    assert r"\{" in out
    assert r"\}" in out
    assert r"\textasciitilde{}" in out
    assert r"\textasciicircum{}" in out


def test_load_intake_example():
    path = Path("examples/intake_example.yaml")
    intake = load_intake(path)
    assert intake.name == "Alex Chen"
    assert "Git" not in intake.skills
    assert "Python" in intake.skills
    assert intake_norms_bucket(intake) == "swe_intern"


def test_fabrication_g1_git_to_suggestions_not_resume():
    """Omit Git in intake; norms say Git >50% → suggestions only (G1)."""
    intake = load_intake(Path("examples/intake_example.yaml"))
    assert "git" not in {s.lower() for s in intake.skills}

    annotated = _annotated(
        skills={
            "Languages": ["Python", "TypeScript"],
            "Developer Tools": ["Git", "Docker"],  # fabricated Git
        }
    )
    prevalence = {"Git": 0.7237, "Python": 0.8684, "Docker": 0.5132}

    out = enforce_g1(annotated, intake, prevalence)

    flat = [s for items in out.resume.skills.values() for s in items]
    assert not any(s.lower() == "git" for s in flat), flat
    assert "Docker" in flat
    assert "Python" in flat

    missing = [s for s in out.suggestions if s.type == "missing_skill"]
    assert any("Git" in s.detail and "%" in s.detail for s in missing), missing


def test_compound_git_skill_not_suggested():
    """Git/GitHub Actions CI in intake/resume must count as owning Git."""
    intake = Intake(
        name="Erfan",
        target_role="Software Engineer",
        year="year_1",
        has_internships=True,
        skills=["Python", "Git/GitHub Actions CI", "Docker"],
    )
    owned = expand_skill_tokens(intake.skills)
    assert skill_is_owned("Git", owned)

    annotated = _annotated(
        skills={
            "Languages": ["Python"],
            "Developer Tools": ["Git/GitHub Actions CI", "Docker"],
        },
        suggestions=[
            Suggestion(
                type="missing_skill",
                detail="Consider adding Git, Java, HTML to showcase breadth.",
            )
        ],
    )
    out = enforce_g1(annotated, intake, {"Git": 0.72, "Python": 0.86, "Java": 0.58})
    missing = [s for s in out.suggestions if s.type == "missing_skill"]
    assert not any("Git" in s.detail for s in missing), missing
    assert any("Java" in s.detail and "58%" in s.detail for s in missing), missing
    # LLM padding suggestion discarded
    assert not any("showcase breadth" in s.detail for s in out.suggestions)


def test_enforce_g1_no_longer_emits_metric_scope_suggestions():
    """Post-refactor: bullet metric/scope weaknesses are the critic's job, not
    Phase 4 suggestions. enforce_g1 must not emit missing_metric/content_gap."""
    annotated = _annotated(
        experience_bullets=[
            AnnotatedBullet(
                text=_long(
                    "Triaged production pipeline failures and landed fixes for incident reports"
                ),
                rewritten_from="Resolved production issues",
                gaps=["no_metric", "vague_scope"],
            )
        ]
    )
    intake = load_intake(Path("examples/intake_example.yaml"))
    out = enforce_g1(annotated, intake, {"Python": 0.86})
    assert not any(s.type == "missing_metric" for s in out.suggestions)
    assert not any(s.type == "content_gap" for s in out.suggestions)


def test_bullet_gap_hints_detect_weak_bullets():
    """The critic's deterministic hint detector flags no-metric/vague bullets."""
    from src.generation.critic import bullet_gap_hints
    from src.schemas import ResumeContent

    resume = ResumeContent.model_validate(
        {
            "contact": {"name": "T"},
            "education": [],
            "experience": [
                {
                    "company": "Acme",
                    "title": "X",
                    "dates": "2023",
                    "location": "",
                    "bullets": [
                        "Worked on various backend things to help the team and improve stuff.",
                        "Cut p95 latency from 1.8s to 1.4s on the checkout path for end users.",
                    ],
                }
            ],
            "projects": [],
            "skills": {"L": ["Python"]},
            "section_order": ["experience", "skills"],
        }
    )
    hints = bullet_gap_hints(resume)
    assert len(hints) == 1
    assert "no_metric" in hints[0]["gaps"]
    assert "vague_scope" in hints[0]["gaps"]


def test_norms_block_marks_absent_git():
    owned = expand_skill_tokens(["Python"])
    block = format_norms_block({"Git": 0.72, "Python": 0.86}, owned, bucket="swe_intern")
    assert "Git" in block
    assert "ABSENT" in block
    assert "PRESENT" in block
    assert "Core" in block


def test_norms_block_compound_git_present():
    owned = expand_skill_tokens(["Git/GitHub Actions CI"])
    block = format_norms_block({"Git": 0.72}, owned, bucket="swe_intern")
    assert "[PRESENT]" in block


def test_tiered_skill_gap_includes_common():
    from src.generation.generator import build_missing_skill_suggestions

    owned = expand_skill_tokens(["Python"])
    sugs = build_missing_skill_suggestions(
        {"Python": 0.86, "Git": 0.72, "Redis": 0.30, "Obscure": 0.10},
        owned,
        bucket="swe_intern",
    )
    details = " ".join(s.detail for s in sugs)
    assert "Git" in details and "core gap" in details
    assert "Redis" in details and "common gap" in details
    assert "Obscure" not in details


def test_allowed_skills_includes_project_tech():
    intake = Intake(
        name="T",
        skills=["Python"],
        projects=[
            {
                "name": "P",
                "technologies": "React, Node.js",
                "description": "desc",
            }
        ],
    )
    allowed = allowed_skills(intake)
    assert "python" in allowed
    assert "react" in allowed
    assert "nodejs" in allowed


def test_fluff_lint_catches_banned_words():
    assert find_fluff_hits("Packaged for seamless macOS distribution") == ["seamless"]
    assert find_fluff_hits("Built a robust RBAC system to ensure integrity") == [
        "robust",
        "ensure",
    ]
    dirty = _minimal_resume(
        projects=[
            {
                "name": "Bina",
                "technologies": "Python",
                "dates": "2025",
                "bullets": [
                    _long("Packaged the application using PyInstaller for seamless macOS distribution")
                ],
            }
        ]
    )
    violations = lint_resume_fluff(dirty)
    assert violations
    assert any("seamless" in v for v in violations)

    clean = _minimal_resume()
    assert lint_resume_fluff(clean) == []


def test_annotated_to_resume_flattens_bullets():
    ann = _annotated()
    resume = annotated_to_resume(ann.resume)
    assert isinstance(resume.experience[0]["bullets"][0], str)


def test_render_tex_contains_name_and_escaped_ampersand():
    resume = _minimal_resume(
        experience=[
            {
                "company": "A & B Labs",
                "title": "Intern",
                "dates": "2025",
                "location": "TX",
                "bullets": [
                    _long("Shipped features for the campus equipment checkout platform"),
                ],
            }
        ]
    )
    tex = render_tex(resume)
    assert "Alex Chen" in tex
    assert r"A \& B Labs" in tex
    assert "\\begin{document}" in tex
    assert "Technical Skills" in tex


def test_hard_trim_reduces_bullets():
    resume = _minimal_resume(
        projects=[
            {
                "name": "P1",
                "technologies": "Python",
                "dates": "2024",
                "bullets": [
                    _long("First project bullet describing substantial shipped work"),
                    _long("Second project bullet describing substantial shipped work"),
                    _long("Third project bullet describing substantial shipped work"),
                ],
            },
            {
                "name": "P2",
                "technologies": "Go",
                "dates": "2024",
                "bullets": [
                    _long("Another project bullet describing substantial shipped work"),
                ],
            },
        ]
    )
    before = total_bullets(resume)
    trimmed = hard_trim(resume)
    assert total_bullets(trimmed) < before


def test_compile_smoke(tmp_path: Path):
    """Optional: compile a tiny resume if tectonic is available."""
    import shutil

    if not shutil.which("tectonic"):
        pytest.skip("tectonic not installed")

    from src.generation.renderer import compile_pdf, count_pdf_pages, render_tex

    resume = _minimal_resume()
    tex = render_tex(resume)
    pdf = compile_pdf(tex, tmp_path, basename="smoke")
    assert pdf.is_file()
    assert count_pdf_pages(pdf) >= 1


def test_pagefit_hard_trim_on_fat_resume(tmp_path: Path):
    """Deliberately overstuffed ResumeContent → hard_trim until ≤1 page."""
    import shutil

    if not shutil.which("tectonic"):
        pytest.skip("tectonic not installed")

    from src.generation.pagefit import hard_trim
    from src.generation.renderer import compile_pdf, count_pdf_pages, render_tex

    fat_bullets = [
        _long(
            f"Delivered feature set number {i} with measurable impact across services "
            f"and documented rollout notes for on-call engineers carefully"
        )
        for i in range(4)
    ]
    projects = [
        {
            "name": f"Project {i}",
            "technologies": "Python, Docker, Kubernetes, TypeScript, React",
            "dates": "2024 -- 2025",
            "bullets": [
                _long(
                    f"Built subsystem {j} with observability hooks and load tests under peak traffic"
                )
                for j in range(3)
            ],
        }
        for i in range(4)
    ]
    experience = [
        {
            "company": f"Company {i}",
            "title": "Software Engineering Intern",
            "dates": "May 2024 -- Aug 2024",
            "location": "Remote",
            "bullets": list(fat_bullets),
        }
        for i in range(4)
    ]
    fat = ResumeContent.model_construct(
        contact={
            "name": "Fat Resume",
            "email": "f@e.com",
            "phone": "1",
            "linkedin": "",
            "github": "",
            "website": "",
        },
        education=[
            {
                "school": "Big U",
                "degree": "BS CS",
                "dates": "2022--Present",
                "location": "WA",
                "details": "Lots of coursework and honors listed here for density.",
            }
        ],
        experience=experience,
        projects=projects,
        skills={
            "Languages": ["Python", "Go", "TypeScript", "Java", "C++"],
            "Frameworks": ["React", "FastAPI", "Flask", "Node.js"],
            "Developer Tools": ["Docker", "Kubernetes", "Git", "AWS"],
        },
        section_order=["education", "experience", "projects", "skills"],
    )
    pdf = compile_pdf(render_tex(fat), tmp_path, basename="fat")
    assert count_pdf_pages(pdf) > 1

    content = fat
    pages = 99
    for _ in range(20):
        content = hard_trim(content)
        pdf = compile_pdf(render_tex(content), tmp_path, basename="fat")
        pages = count_pdf_pages(pdf)
        if pages <= 1:
            break
    assert pages <= 1
