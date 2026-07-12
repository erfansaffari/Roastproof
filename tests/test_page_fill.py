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


def test_infer_experience_technologies_when_llm_blank():
    from src.generation.generator import (
        infer_technologies_from_blob,
        resolve_entry_technologies,
        technology_vocab_from_intake,
    )

    intake = _intake(
        skills=["React", "Next.js", "OpenAI API", "Python", "TypeScript"],
        experience=[
            {
                "company": "ErgoClean",
                "title": "SWE",
                "dates": "2024",
                "description": (
                    "Built an AI lead-generation agent. Shipped a dashboard in React and Next.js. "
                    "Automated support with an n8n + OpenAI API + Resend pipeline."
                ),
            }
        ],
    )
    vocab = technology_vocab_from_intake(intake)
    blob = intake.experience[0].description
    inferred = infer_technologies_from_blob(blob, vocab)
    low = inferred.lower()
    assert "react" in low
    assert "next.js" in low or "nextjs" in low.replace(".", "")
    assert "openai" in low
    assert "n8n" in low
    assert "resend" in low
    # Empty LLM tech → still resolves via inference
    resolved = resolve_entry_technologies("", blob, vocab)
    assert "React" in resolved or "react" in resolved.lower()
    assert resolved  # must not stay empty


def test_elicit_skips_metrics_already_in_intake():
    from src.generation.intake_coverage import (
        autofill_covered_pending,
        covering_quote,
        filter_questions_covered_by_intake,
        format_intake_metrics_block,
        question_covered_by_intake,
    )
    from src.schemas import ElicitationQuestion, QAEntry, QAStore

    intake = _intake(
        experience=[
            {
                "company": "ErgoClean",
                "title": "SWE",
                "dates": "2024",
                "description": (
                    "Built an AI lead-generation agent generating 50+ qualified leads per week. "
                    "Cut response time by 80% while handling 500+ monthly inquiries."
                ),
            },
            {
                "company": "SchoolTalk",
                "title": "Founder",
                "dates": "2024",
                "description": (
                    "Built a multi-tenant SaaS from 0 to 100+ active users across 3 schools."
                ),
            },
        ],
        projects=[
            {
                "name": "kv-store",
                "technologies": "Go",
                "description": "Implemented Raft consensus across three nodes.",
            }
        ],
    )
    block = format_intake_metrics_block(intake)
    assert "50+" in block
    assert "100+" in block

    qs = [
        ElicitationQuestion(
            topic="missing_metric",
            impact="high",
            question="What was the percentage increase in lead generation at ErgoClean?",
            relates_to="ErgoClean - AI lead-generation agent",
        ),
        ElicitationQuestion(
            topic="missing_metric",
            impact="high",
            question="Can you provide the total number of users on the SchoolTalk platform?",
            relates_to="SchoolTalk - multi-tenant SaaS community platform",
        ),
        ElicitationQuestion(
            topic="missing_metric",
            impact="high",
            question="How many nodes does the chaos test kill in kv-store under partition?",
            relates_to="kv-store",
        ),
    ]
    kept = filter_questions_covered_by_intake(qs, intake)
    # First two covered by intake; third may survive (no matching metric about nodes killed)
    assert all("ErgoClean" not in (q.relates_to or "") for q in kept)
    assert all("SchoolTalk" not in (q.relates_to or "") for q in kept)

    store = QAStore(
        round=1,
        questions=[
            QAEntry(
                id="q1",
                topic="missing_metric",
                question="total number of users on SchoolTalk?",
                relates_to="SchoolTalk",
                answer=None,
                status="pending",
            )
        ],
    )
    filled = autofill_covered_pending(store, intake)
    assert filled.questions[0].status == "answered"
    assert "100" in (filled.questions[0].answer or "")
    assert covering_quote(intake, qs[1].question, qs[1].relates_to)


def test_coverage_is_dimension_precise_not_any_digit():
    """Users question must NOT be covered just because the entry has a leads number."""
    from src.generation.intake_coverage import (
        covering_quote,
        filter_questions_covered_by_intake,
        question_covered_by_intake,
    )
    from src.schemas import ElicitationQuestion

    intake = _intake(
        experience=[
            {
                "company": "ErgoClean",
                "title": "SWE",
                "dates": "2024",
                "description": (
                    "Built an AI lead-generation agent generating 50+ qualified leads per week."
                ),
            }
        ],
    )
    users_q = ElicitationQuestion(
        topic="missing_metric",
        impact="high",
        question="How many end users actually used the ErgoClean dashboard?",
        relates_to="ErgoClean",
    )
    leads_q = ElicitationQuestion(
        topic="missing_metric",
        impact="high",
        question="How many qualified leads per week did the agent generate?",
        relates_to="ErgoClean",
    )
    ownership_q = ElicitationQuestion(
        topic="vague_scope",
        impact="high",
        question="Which parts of the agent pipeline did you personally own?",
        relates_to="ErgoClean — community ownership theme",
    )
    assert covering_quote(intake, users_q.question, users_q.relates_to) is None
    assert covering_quote(intake, leads_q.question, leads_q.relates_to)
    assert not question_covered_by_intake(intake, ownership_q)
    kept = filter_questions_covered_by_intake([users_q, leads_q, ownership_q], intake)
    assert len(kept) == 2
    assert {q.topic for q in kept} == {"missing_metric", "vague_scope"}
    assert any("users" in q.question.lower() for q in kept)


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
