"""Unit tests for elicitation memory, QA sidecar, convergence, project-eval stability."""

from __future__ import annotations

from pathlib import Path

import yaml

from src.generation.generator import format_answers_block, suggestions_from_bullet_gaps
from src.generation.project_eval import drop_ungrounded, quote_in_block
from src.generation.prompts import elicit_system, elicit_user
from src.generation.qa_store import (
    append_new_questions,
    filter_by_round_impact,
    format_answers_block_from_store,
    format_history_block,
    load_qa_store,
    merge_legacy_answers,
    normalize_question_text,
    save_qa_store,
    semantic_dedup_questions,
    should_stop_elicitation,
    sidecar_path,
    stable_question_id,
)
from src.schemas import (
    AnnotatedBullet,
    AnnotatedExperience,
    AnnotatedProject,
    AnnotatedResume,
    ElicitationQuestion,
    FieldGap,
    Intake,
    ProjectEvalResult,
    ProjectVerdict,
    QAEntry,
    QAStore,
)


def _intake(**kwargs) -> Intake:
    base = dict(
        name="T",
        target_role="Software Engineer",
        year="year_2",
        has_internships=True,
        skills=["Python"],
        projects=[{"name": "P", "technologies": "Go", "description": "kv store"}],
    )
    base.update(kwargs)
    return Intake.model_validate(base)


def test_sidecar_path():
    assert sidecar_path(Path("examples/my_intake.yaml")) == Path(
        "examples/my_intake.qa.yaml"
    )


def test_stable_question_id_is_content_hash():
    a = stable_question_id("How many users did SchoolTalk reach?", "SchoolTalk")
    b = stable_question_id("How many users did SchoolTalk reach?", "SchoolTalk")
    c = stable_question_id("What was the response time drop?", "ErgoClean")
    assert a == b
    assert a != c
    assert not a.startswith("q1")


def test_qa_store_round_trip(tmp_path: Path):
    path = tmp_path / "x.qa.yaml"
    store = QAStore(
        round=1,
        converged=False,
        questions=[
            QAEntry(
                id="metric-abc123",
                round=1,
                topic="missing_metric",
                impact="high",
                question="How many monthly inquiries?",
                relates_to="ErgoClean - pipeline",
                answer=None,
                status="pending",
            )
        ],
    )
    save_qa_store(store, path)
    loaded = load_qa_store(path)
    assert loaded.round == 1
    assert loaded.questions[0].status == "pending"
    assert loaded.questions[0].answer is None

    data = yaml.safe_load(path.read_text())
    data["questions"][0]["answer"] = "500+ monthly"
    path.write_text(yaml.safe_dump(data))
    loaded2 = load_qa_store(path)
    assert loaded2.questions[0].status == "answered"
    assert "500" in (loaded2.questions[0].answer or "")


def test_decline_token_marks_declined(tmp_path: Path):
    path = tmp_path / "x.qa.yaml"
    store = QAStore(
        questions=[
            QAEntry(
                id="q",
                question="Any chaos-test numbers?",
                relates_to="raft",
                answer="skip",
                status="pending",
            )
        ]
    )
    save_qa_store(store, path)
    loaded = load_qa_store(path)
    assert loaded.questions[0].status == "declined"


def test_merge_legacy_answers():
    store = QAStore(
        questions=[
            QAEntry(
                id="q1",
                question="How many users?",
                relates_to="SchoolTalk",
                answer=None,
                status="pending",
            )
        ]
    )
    intake = _intake(answers={"q1": "100+ users", "q99": "orphan fact"})
    merged = merge_legacy_answers(store, intake)
    by_id = {q.id: q for q in merged.questions}
    assert by_id["q1"].status == "answered"
    assert by_id["q1"].answer == "100+ users"
    assert "q99" in by_id
    assert by_id["q99"].status == "answered"


def test_semantic_dedup_drops_rephrase(monkeypatch):
    prior = [
        QAEntry(
            id="old",
            question="How many monthly inquiries did the ErgoClean pipeline handle?",
            relates_to="ErgoClean",
        )
    ]
    new = [
        ElicitationQuestion(
            topic="missing_metric",
            impact="high",
            question=(
                "What was the volume of monthly inquiries processed by "
                "ErgoClean's automation pipeline?"
            ),
            relates_to="ErgoClean",
        ),
        ElicitationQuestion(
            topic="missing_metric",
            impact="high",
            question="How many Raft nodes did you run chaos tests against?",
            relates_to="raft-kv-store",
        ),
    ]

    def fake_embed(texts):
        return [
            [1.0, 0.0, 0.0],
            [0.95, 0.1, 0.0],
            [0.0, 1.0, 0.0],
        ]

    monkeypatch.setattr("src.generation.qa_store._embed_texts", fake_embed)
    kept = semantic_dedup_questions(new, prior, threshold=0.8)
    assert len(kept) == 1
    assert "Raft" in kept[0].question


def test_semantic_dedup_fallback_exact_match(monkeypatch):
    prior = [QAEntry(id="old", question="How many users on SchoolTalk?")]
    new = [
        ElicitationQuestion(
            topic="missing_metric",
            impact="high",
            question="How many users on SchoolTalk?",
            relates_to="SchoolTalk",
        ),
        ElicitationQuestion(
            topic="missing_metric",
            impact="high",
            question="What was engagement growth?",
            relates_to="SchoolTalk",
        ),
    ]

    def boom(_):
        raise RuntimeError("no embedder")

    monkeypatch.setattr("src.generation.qa_store._embed_texts", boom)
    kept = semantic_dedup_questions(new, prior)
    assert len(kept) == 1
    assert "engagement" in kept[0].question.lower()


def test_filter_by_round_impact():
    qs = [
        ElicitationQuestion(
            topic="missing_metric", impact="high", question="A", relates_to=""
        ),
        ElicitationQuestion(
            topic="vague_scope", impact="medium", question="B", relates_to=""
        ),
    ]
    assert len(filter_by_round_impact(qs, next_round=1)) == 2
    assert len(filter_by_round_impact(qs, next_round=2)) == 1


def test_stopping_rule_complete_and_budget():
    store = QAStore(
        round=1,
        questions=[QAEntry(id="a", question="q", answer="yes", status="answered")],
    )
    stop, reason = should_stop_elicitation(
        store, model_complete=True, new_surviving=0
    )
    assert stop
    assert "complete" in reason or "no new" in reason

    store2 = QAStore(round=3, questions=[])
    stop2, reason2 = should_stop_elicitation(
        store2, model_complete=False, new_surviving=1, max_rounds=3
    )
    assert stop2
    assert "max" in reason2

    # Round 1 + dropped_covered > 0 must NOT latch (over-filter failure mode)
    store3 = QAStore(round=0, questions=[])
    stop3, _ = should_stop_elicitation(
        store3,
        model_complete=True,
        new_surviving=0,
        dropped_covered=2,
        next_round=1,
    )
    assert not stop3


def test_reopen_if_stale_empty_convergence_and_intake_hash():
    from src.generation.qa_store import reopen_if_stale

    stale = QAStore(round=1, converged=True, questions=[], intake_hash="")
    opened, reason = reopen_if_stale(stale, current_intake_hash="abc123")
    assert not opened.converged
    assert "stale" in reason.lower() or "re-open" in reason.lower()
    assert opened.intake_hash == "abc123"

    changed = QAStore(
        round=2,
        converged=True,
        intake_hash="oldhash",
        questions=[QAEntry(id="a", question="q", answer="1", status="answered")],
    )
    reopened, reason2 = reopen_if_stale(changed, current_intake_hash="newhash")
    assert not reopened.converged
    assert "intake" in reason2.lower()
    assert reopened.intake_hash == "newhash"


def test_append_assigns_stable_ids():
    store = QAStore()
    qs = [
        ElicitationQuestion(
            id="q1",
            topic="missing_metric",
            impact="high",
            question="How many SchoolTalk users?",
            relates_to="SchoolTalk",
        )
    ]
    updated = append_new_questions(store, qs, round_num=1)
    assert updated.round == 1
    assert len(updated.questions) == 1
    assert updated.questions[0].id != "q1"
    assert updated.questions[0].status == "pending"


def test_history_and_answers_blocks():
    store = QAStore(
        questions=[
            QAEntry(
                id="a",
                question="What % did response time drop?",
                relates_to="ErgoClean inquiries",
                answer="80%, across 500+ monthly",
                status="answered",
            ),
            QAEntry(
                id="b",
                question="Any chaos-test numbers?",
                relates_to="raft-kv-store chaos",
                answer="skip",
                status="declined",
            ),
            QAEntry(
                id="c",
                question="Engagement metrics for SchoolTalk?",
                relates_to="SchoolTalk",
                answer=None,
                status="pending",
            ),
        ]
    )
    hist = format_history_block(store)
    assert "80%" in hist
    assert "declined" in hist.lower()
    assert "pending" in hist.lower()

    block = format_answers_block_from_store(store)
    assert 'Q: "What % did response time drop?"' in block
    assert "80%" in block
    assert "do NOT invent" in block

    intake = _intake()
    via_gen = format_answers_block(intake, qa_store=store)
    assert "80%" in via_gen


def test_declined_suppresses_missing_metric_suggestion():
    annotated = AnnotatedResume(
        contact={"name": "T"},
        education=[],
        experience=[
            AnnotatedExperience(
                company="ErgoClean",
                title="SWE",
                dates="2024",
                bullets=[
                    AnnotatedBullet(
                        text="Debugged production issues across the pipeline.",
                        rewritten_from="resolved production issues",
                        gaps=["no_metric"],
                    )
                ],
            )
        ],
        projects=[
            AnnotatedProject(
                name="raft-kv-store",
                bullets=[
                    AnnotatedBullet(
                        text="Wrote network-partition chaos tests.",
                        rewritten_from="chaos tests",
                        gaps=["no_metric"],
                    )
                ],
            )
        ],
        skills={},
        section_order=["experience", "projects", "skills"],
    )
    all_sugs = suggestions_from_bullet_gaps(annotated)
    assert len([s for s in all_sugs if s.type == "missing_metric"]) == 2

    filtered = suggestions_from_bullet_gaps(
        annotated, declined_needles=["raft-kv-store"]
    )
    metrics = [s for s in filtered if s.type == "missing_metric"]
    assert len(metrics) == 1
    assert "ErgoClean" in metrics[0].detail


def test_elicit_prompt_includes_history():
    intake = _intake()
    sys = elicit_system(intake, next_round=1)
    assert "complete" in sys.lower()
    assert "impact" in sys.lower()
    assert "round 1" in sys.lower()
    assert "2–4" in sys or "2-4" in sys
    user = elicit_user(
        intake,
        history_block='- [a] Q: How many?\n  A: 100',
        critiques_block="1. id=x:1 [experience/metrics] avoid unowned we-built claims",
    )
    assert "Prior Q&A history" in user
    assert "How many?" in user
    assert "100" in user
    assert "Retrieved community critiques" in user
    assert "unowned" in user


def test_drop_ungrounded_rejects_rule_only_when_ids_exist():
    raw = ProjectEvalResult(
        projects=[
            ProjectVerdict(
                name="Good",
                verdict="strong_keep",
                rationale="solid systems depth",
                improvements=["Add chaos-test metrics"],
                evidence_ids=["abc:1"],
            ),
            ProjectVerdict(
                name="Lazy",
                verdict="strong_keep",
                rationale="looks fine",
                improvements=[],
                evidence_ids=["rule:project_selection/projects"],
            ),
        ],
        field_gaps=[],
    )
    cleaned = drop_ungrounded(raw, {"abc:1"}, critiques_block="1. id=abc:1 stuff")
    assert [p.name for p in cleaned.projects] == ["Good"]


def test_quote_in_block_and_field_gap_drop():
    block = (
        "1. id=abc:1 [projects/selection] (score=0.9) "
        "You need more traditional backend projects like C++ or Java systems work."
    )
    assert quote_in_block("traditional backend projects like C++", block)
    assert not quote_in_block("frontend React portfolio gap", block)
    assert not quote_in_block("short", block)

    raw = ProjectEvalResult(
        projects=[
            ProjectVerdict(
                name="Good",
                verdict="strong_keep",
                rationale="solid",
                evidence_ids=["abc:1"],
            )
        ],
        field_gaps=[
            FieldGap(
                gap="Traditional backend (C++/Java)",
                evidence_ids=["abc:1"],
                evidence_quote="traditional backend projects like C++",
            ),
            FieldGap(
                gap="More frontend apps",
                evidence_ids=["abc:1"],
                evidence_quote="you need more React frontend projects",
            ),
            FieldGap(
                gap="No quote gap",
                evidence_ids=["abc:1"],
                evidence_quote="",
            ),
        ],
    )
    cleaned = drop_ungrounded(raw, {"abc:1"}, critiques_block=block)
    assert len(cleaned.field_gaps) == 1
    assert "backend" in cleaned.field_gaps[0].gap.lower()


def test_normalize_question_text():
    assert normalize_question_text("  How MANY users?! ") == "how many users"
