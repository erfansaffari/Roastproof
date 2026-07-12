"""Unit tests for Phase 4.8 page-fill measurement, unused facts, expand loop helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from src.generation.pagefit import (
    thin_entries,
    unused_intake_facts,
)
from src.generation.prompts import (
    expand_elicit_system,
    pagefit_expand_instruction,
)
from src.generation.qa_store import append_new_questions, semantic_dedup_questions
from src.knowledge.norms import _percentile, compute_norms
from src.schemas import (
    ElicitationQuestion,
    GenerationResult,
    Intake,
    QAEntry,
    QAStore,
    QualityFlags,
    ResumeContent,
    TargetRole,
    ThreadRecord,
)


def _intake(**kwargs) -> Intake:
    base = dict(
        name="T",
        target_role="Software Engineer",
        year="year_2",
        has_internships=True,
        skills=["Python", "Go"],
        experience=[
            {
                "company": "Acme",
                "title": "Intern",
                "dates": "2024",
                "description": (
                    "Built an API gateway in Go serving 10k requests per day. "
                    "Added Redis caching that cut p99 latency by 40 percent. "
                    "Wrote integration tests covering the auth path."
                ),
            }
        ],
        projects=[
            {
                "name": "kv-store",
                "technologies": "Go",
                "description": (
                    "Implemented Raft consensus across three nodes. "
                    "Added chaos tests for network partitions."
                ),
            }
        ],
    )
    base.update(kwargs)
    return Intake.model_validate(base)


def _resume(exp_bullets: list[str], proj_bullets: list[str]) -> ResumeContent:
    return ResumeContent.model_construct(
        contact={"name": "T"},
        education=[],
        experience=[
            {
                "company": "Acme",
                "title": "Intern",
                "dates": "2024",
                "location": "",
                "bullets": exp_bullets,
            }
        ],
        projects=[
            {
                "name": "kv-store",
                "technologies": "Go",
                "dates": "",
                "bullets": proj_bullets,
            }
        ],
        skills={"Languages": ["Python", "Go"]},
        section_order=["experience", "projects", "skills"],
    )


def test_percentile():
    assert _percentile([1.0, 2.0, 3.0, 4.0], 50) == 2.5
    assert _percentile([2.0, 2.0, 2.0, 4.0], 75) >= 2.0
    assert _percentile([], 75) == 0.0


def test_compute_norms_includes_p75():
    records = []
    for i, counts in enumerate([[2, 2], [3, 3], [4, 2], [1, 1]]):
        exp_lines = "\n".join(f"- bullet number {j} for entry" for j in range(counts[0]))
        proj_lines = "\n".join(f"- project bullet {j} here" for j in range(counts[1]))
        text = (
            f"EXPERIENCE\nSoftware Engineer Intern\n{exp_lines}\n\n"
            f"PROJECTS\nCool Project\n{proj_lines}\n\n"
            f"SKILLS\nPython, C++\n"
        )
        records.append(
            ThreadRecord(
                thread_id=f"t{i}",
                target_role=TargetRole.SOFTWARE_ENGINEER,
                applicant_profile="year 2 intern",
                resume_text=text,
                resume_sections={
                    "experience": f"Software Engineer Intern\n{exp_lines}",
                    "projects": f"Cool Project\n{proj_lines}",
                    "skills": "Python, C++",
                },
                context_message="looking for swe internship",
                critiques=[],
                quality_flags=QualityFlags(),
            )
        )
    norms = compute_norms(records, min_n=1)
    role_entries = list(norms["roles"].values())
    assert role_entries
    entry = role_entries[0]
    assert "bullets_per_entry_p75" in entry
    assert "total_bullets_median" in entry
    assert "total_bullets_p75" in entry
    assert entry["bullets_per_entry_p75"] >= entry["median_bullets_per_entry"]


def test_unused_intake_facts_detects_uncovered():
    intake = _intake()
    resume = _resume(
        [
            "Built a Go API gateway that served about ten thousand requests daily for clients.",
        ],
        [
            "Implemented Raft consensus on a three-node cluster with leader election.",
        ],
    )
    result = GenerationResult(resume=resume, suggestions=[])
    unused = unused_intake_facts(intake, result)
    joined = " ".join(unused).lower()
    assert "redis" in joined or "caching" in joined or "latency" in joined
    assert "chaos" in joined or "partition" in joined or "tests" in joined


def test_unused_facts_empty_when_all_covered():
    intake = _intake(
        experience=[
            {
                "company": "Acme",
                "title": "Intern",
                "dates": "2024",
                "description": "Built an API gateway in Go serving 10k requests per day.",
            }
        ],
        projects=[
            {
                "name": "kv-store",
                "technologies": "Go",
                "description": "Implemented Raft consensus across three nodes.",
            }
        ],
    )
    resume = _resume(
        ["Built an API gateway in Go serving 10k requests per day for production traffic."],
        ["Implemented Raft consensus across three nodes with persistent logs."],
    )
    unused = unused_intake_facts(intake, GenerationResult(resume=resume, suggestions=[]))
    assert len(unused) <= 1


def test_thin_entries():
    resume = _resume(
        ["Built a Go API gateway that served ten thousand requests daily for clients."],
        [
            "Implemented Raft consensus on a three-node cluster with leader election.",
            "Added chaos tests verifying recovery after network partitions heal cleanly.",
        ],
    )
    thin = thin_entries(resume, target_per_entry=3.0)
    assert any("Acme" in t for t in thin)


def test_expand_instruction_includes_unused_and_fill():
    role = MagicMock()
    role.display_name = "Software Engineer"
    text = pagefit_expand_instruction(
        fill_ratio=0.72,
        fill_target=0.85,
        bullet_count=8,
        thin_entries=["experience `Acme`: 1 bullets (target ≥3)"],
        unused_facts=["[Acme] Added Redis caching that cut p99 latency by 40 percent."],
        role=role,
        bullets_per_entry_p75=3.0,
    )
    assert "72%" in text
    assert "85%" in text
    assert "Redis" in text
    assert "Acme" in text
    assert "never invent" in text.lower() or "Do NOT invent" in text


def test_expand_elicit_prompt_invariants():
    intake = _intake()
    sys = expand_elicit_system(intake)
    assert "expand_content" in sys


def test_expansion_questions_append_even_when_converged(monkeypatch):
    store = QAStore(
        round=3,
        converged=True,
        questions=[
            QAEntry(
                id="old",
                question="How many users?",
                answer="100",
                status="answered",
            )
        ],
    )
    new = [
        ElicitationQuestion(
            topic="expand_content",
            impact="high",
            question="What testing or deployment work did you do at Acme beyond the API gateway?",
            relates_to="Acme",
        )
    ]

    def fake_embed(texts):
        return [[float(i), 0.0, 0.0] for i in range(len(texts))]

    monkeypatch.setattr("src.generation.qa_store._embed_texts", fake_embed)
    surviving = semantic_dedup_questions(new, store.questions)
    assert len(surviving) == 1
    updated = append_new_questions(store, surviving, round_num=4)
    updated = updated.model_copy(update={"converged": False})
    assert updated.converged is False
    assert any(q.status == "pending" for q in updated.questions)


def test_unused_qa_answer_survives_partial_overlap():
    """Elicited QA answers use a stricter threshold so a related bullet doesn't swallow them."""
    intake = _intake(
        experience=[
            {
                "company": "SindadSec",
                "title": "DevOps Intern",
                "dates": "2022",
                "description": "Deployed Docker security tooling and configured Linux servers.",
            }
        ],
        projects=[
            {
                "name": "kv-store",
                "technologies": "Go",
                "description": "Implemented Raft consensus across three nodes.",
            }
        ],
    )
    resume = _resume(
        [
            "Configured Linux with Nginx, UFW, and fail2ban against brute-force attacks.",
        ],
        [
            "Implemented Raft consensus on a three-node cluster with leader election.",
        ],
    )
    # Patch company name on resume to match
    resume.experience[0]["company"] = "SindadSec"
    result = GenerationResult(resume=resume, suggestions=[])
    answer = (
        "Faced repeated SSH brute-force attempts; fixed with key-only auth and fail2ban."
    )
    unused = unused_intake_facts(
        intake,
        result,
        qa_entries=[
            {
                "answer": answer,
                "relates_to": "SindadSec",
            }
        ],
    )
    joined = " ".join(unused).lower()
    assert "ssh" in joined or "key-only" in joined
    assert any("SindadSec" in u for u in unused)


def test_ground_technologies_rejects_unattested_tools():
    from src.generation.generator import ground_technologies

    blob = (
        "Architected Python backend with Docker and Kubernetes. "
        "Built FastAPI microservices with Celery and RabbitMQ."
    )
    grounded = ground_technologies(
        "Python, FastAPI, Docker, Kubernetes, Celery, Terraform, AWS",
        blob,
    )
    low = grounded.lower()
    assert "python" in low
    assert "fastapi" in low
    assert "docker" in low
    assert "terraform" not in low
    assert "aws" not in low


def test_render_technologies_on_own_line():
    from src.generation.renderer import render_tex

    resume = ResumeContent.model_construct(
        contact={"name": "T"},
        education=[],
        experience=[
            {
                "company": "Acme",
                "title": "Intern",
                "dates": "2024",
                "location": "",
                "technologies": "Python, FastAPI, Docker",
                "bullets": [
                    "Built a Go API gateway that served about ten thousand requests daily for clients."
                ],
            }
        ],
        projects=[
            {
                "name": "kv-store",
                "technologies": "Go, Raft, Docker",
                "dates": "",
                "bullets": [
                    "Implemented Raft consensus on a three-node cluster with leader election."
                ],
            }
        ],
        skills={"Languages": ["Python", "Go"]},
        section_order=["experience", "projects", "skills"],
    )
    tex = render_tex(resume)
    assert r"\item[] \textit{\small" in tex
    assert r"\cdot" in tex
    assert r"\vspace{4pt}" in tex
    # Project tech must NOT be inline on the heading row
    assert r"$|$ \emph{Go" not in tex
    assert r"\textbf{kv-store}" in tex
    # space.tex-style: tech line after ItemListStart, before bullets
    assert tex.index(r"\resumeItemListStart") < tex.index(r"\item[] \textit{\small")
    assert "Python" in tex and "FastAPI" in tex and "Docker" in tex


def test_measure_page_fill_on_existing_pdf():
    from src.generation.renderer import measure_page_fill

    candidates = [
        Path("out/mine48/resume.pdf"),
        Path("out/arya3/resume.pdf"),
        Path("out/mine47/resume.pdf"),
    ]
    pdf = next((p for p in candidates if p.is_file()), None)
    if pdf is None:
        return
    fill = measure_page_fill(pdf)
    assert 0.0 < fill <= 1.0
