"""Phase 5 — critic loop + report unit tests (LLM mocked)."""

from __future__ import annotations

import json
from unittest.mock import patch

from src.generation import critic as critic_mod
from src.generation.critic import (
    drop_ungrounded_issues,
    high_med_issues,
    high_severity_issues,
    revise_bullets,
    run_critic,
    ungrounded_high_med,
)
from src.generation.prompts import assert_prompt_invariants, critic_system
from src.generation.report import build_report
from src.schemas import (
    CriticIssue,
    CriticResult,
    Intake,
    QAEntry,
    QAStore,
    ResumeContent,
    RevisionItem,
    RevisionResult,
)

WEAK_BULLET = "Worked on backend stuff to improve the overall system and make it better."
STRONG_BULLET = "Built and maintained backend billing API endpoints in Python for internal tools."


def _intake(**kw) -> Intake:
    base = dict(name="T", target_role="Software Engineer", skills=["Python"])
    base.update(kw)
    return Intake.model_validate(base)


def _resume(bullet: str = WEAK_BULLET) -> ResumeContent:
    return ResumeContent.model_validate(
        {
            "contact": {"name": "T"},
            "education": [],
            "experience": [
                {
                    "company": "Acme",
                    "title": "SWE Intern",
                    "dates": "2023",
                    "location": "",
                    "bullets": [bullet],
                }
            ],
            "projects": [],
            "skills": {"Languages": ["Python"]},
            "section_order": ["experience", "skills"],
        }
    )


# --- 1. prompt invariants ---------------------------------------------------

def test_critic_prompt_invariants():
    sys = critic_system(_intake())
    assert assert_prompt_invariants(sys) == []
    assert "g1" in sys.lower() or "no fabrication" in sys.lower()


# --- 2. grounding filter ----------------------------------------------------

def test_drop_ungrounded_issues():
    r = CriticResult(
        issues=[
            CriticIssue(section="experience", entry="A", bullet_text="x", issue="crit-grounded", severity="high", critique_id="t1:2"),
            CriticIssue(section="experience", entry="A", bullet_text="y", issue="rule-grounded", severity="high", rule_id="rule:3"),
            CriticIssue(section="experience", entry="A", bullet_text="z", issue="substring-id", severity="med", critique_id="t1:2"),
            CriticIssue(section="experience", entry="A", bullet_text="w", issue="ungrounded", severity="high"),
        ]
    )
    kept = drop_ungrounded_issues(r, {"t1:2"}, {"rule:3"})
    issues = {i.issue for i in kept.issues}
    assert "crit-grounded" in issues
    assert "rule-grounded" in issues
    assert "substring-id" in issues
    assert "ungrounded" not in issues
    assert ungrounded_high_med(r, {"t1:2"}, {"rule:3"}) == ["ungrounded"]


# --- 3. targeted revise splices in place ------------------------------------

def test_revise_bullets_splices_in_place():
    resume = _resume()
    issue = CriticIssue(
        section="experience",
        entry="Acme",
        bullet_text=WEAK_BULLET,
        issue="vague scope, no metric",
        severity="high",
        rule_id="rule:1",
    )
    fake = RevisionResult(revisions=[RevisionItem(original=WEAK_BULLET, revised=STRONG_BULLET)])
    with patch("src.generation.critic.complete_json", return_value=fake):
        new_resume, diffs = revise_bullets(_intake(), resume, [issue])
    assert len(diffs) == 1
    assert new_resume.experience[0]["bullets"][0] == STRONG_BULLET
    # Untouched content stays byte-identical.
    assert new_resume.skills == resume.skills
    assert new_resume.contact == resume.contact
    assert diffs[0].original == WEAK_BULLET
    assert diffs[0].revised == STRONG_BULLET


def test_revise_bullets_rejects_instruction_leak():
    resume = _resume()
    issue = CriticIssue(
        section="experience", entry="Acme", bullet_text=WEAK_BULLET,
        issue="no metric", severity="high", rule_id="rule:1",
    )
    leak = "Built backend billing endpoints in Python; specify scale and usage metrics."
    fake = RevisionResult(revisions=[RevisionItem(original=WEAK_BULLET, revised=leak)])
    with patch("src.generation.critic.complete_json", return_value=fake):
        new_resume, diffs = revise_bullets(_intake(), resume, [issue])
    assert diffs == []
    assert new_resume.experience[0]["bullets"][0] == WEAK_BULLET


def test_revise_bullets_rejects_new_fluff():
    resume = _resume()
    issue = CriticIssue(
        section="experience", entry="Acme", bullet_text=WEAK_BULLET,
        issue="x", severity="high", rule_id="rule:1",
    )
    fluffy = "Built a robust, seamless, cutting-edge backend that leverages synergy for users."
    fake = RevisionResult(revisions=[RevisionItem(original=WEAK_BULLET, revised=fluffy)])
    with patch("src.generation.critic.complete_json", return_value=fake):
        new_resume, diffs = revise_bullets(_intake(), resume, [issue])
    # Fluffy rewrite rejected → no change.
    assert diffs == []
    assert new_resume.experience[0]["bullets"][0] == WEAK_BULLET


# --- 4. critic loop fixes seeded weakness -----------------------------------

def test_critic_loop_fixes_seeded_weakness():
    """Simulate the pipeline loop: critic flags weak bullet, revise fixes it,
    second critic pass returns no high-severity issues → loop stops."""
    intake = _intake()
    resume = _resume()

    seeded = CriticResult(
        issues=[
            CriticIssue(
                section="experience", entry="Acme", bullet_text=WEAK_BULLET,
                issue="vague scope, no metric", severity="high",
                suggested_fix="tighten scope", rule_id="rule:1",
            )
        ]
    )
    clean = CriticResult(issues=[])
    fixed = RevisionResult(revisions=[RevisionItem(original=WEAK_BULLET, revised=STRONG_BULLET)])

    critic_calls = {"n": 0}

    def fake_run_critic(_intake_arg, _resume_arg, **kw):
        critic_calls["n"] += 1
        return seeded if critic_calls["n"] == 1 else clean

    with patch("src.generation.critic.run_critic", side_effect=fake_run_critic), \
         patch("src.generation.critic.complete_json", return_value=fixed):
        # Round 0: initial critic
        cr = critic_mod.run_critic(intake, resume)
        rounds = 0
        max_rounds = 2
        log = []
        while high_med_issues(cr) and rounds < max_rounds:
            rounds += 1
            _, diffs = revise_bullets(intake, resume, cr.issues)
            if not diffs:
                break
            resume = _resume(STRONG_BULLET)
            log.append({"round": rounds, "changed": [d.model_dump() for d in diffs]})
            cr = critic_mod.run_critic(intake, resume)

    assert rounds == 1
    assert high_severity_issues(cr) == []
    assert log and log[0]["changed"]
    assert log[0]["changed"][0]["revised"] == STRONG_BULLET


# --- 5. report renders clean markdown ---------------------------------------

def test_report_renders_clean_markdown():
    intake = _intake()
    # Post-refactor: suggestions.json carries only skill gaps + project eval.
    # Bullet metric/scope weaknesses live in the critic section, not here.
    suggestions = [
        {"type": "missing_skill", "detail": "Consider adding Git — commonly expected."},
        {"type": "project_evaluation", "detail": "[KV] verdict=strengthen."},
    ]
    revision_log = [
        {"round": 1, "changed": [
            {"section": "experience", "entry": "Acme", "original": WEAK_BULLET,
             "revised": STRONG_BULLET, "addressed_issue": "vague scope"}
        ]}
    ]
    critic_remaining = [
        {"section": "projects", "entry": "KV", "issue": "no metric", "severity": "med",
         "suggested_fix": "ask for throughput", "rule_id": "rule:2", "critique_id": ""}
    ]
    status = {
        "critic_rounds": 1, "critic_issues_found": 2, "critic_issues_fixed": 1,
        "critic_issues_remaining": 1, "converged": False, "pending_questions": 1, "round": 1,
    }
    qa = QAStore(questions=[QAEntry(id="q1", question="What was the QPS?", status="pending")])
    norms = {"roles": {"swe_intern": {"skill_prevalence": {"Git": 0.6}}}}

    md = build_report(
        intake,
        suggestions=suggestions,
        revision_log=revision_log,
        critic_remaining=critic_remaining,
        status=status,
        qa_store=qa,
        norms=norms,
    )
    # Two-stage structure.
    assert "Stage A — Input review" in md
    assert "Stage B — Output review" in md
    # Stage B sections present.
    assert "Bullet quality (critic pass)" in md
    assert "Project portfolio" in md
    assert "Skills the community expects" in md
    # Metric/scope weaknesses are NOT a standalone suggestion bucket anymore —
    # they belong to the critic section.
    assert "would be stronger with a real number" not in md
    assert "Content gaps" not in md
    # Prevalence traced to norms (60% for Git), no fabricated numbers.
    assert "60%" in md
    assert "norms.json" in md
    # Pending elicitation question surfaced in Stage A.
    assert "What was the QPS?" in md
    # Critic grounding id shown.
    assert "rule:2" in md
    # G1 limitation stated.
    assert "no fabrication" in md.lower()
