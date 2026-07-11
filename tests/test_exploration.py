"""Tests for Phase 2 critique labeling helpers."""

from src.knowledge.exploration import (
    is_op_reply,
    heuristic_critique_category,
    other_rate,
    label_critiques,
    _parse_label_batch,
)
from src.schemas import ThreadRecord, TargetRole, Critique


def test_is_op_reply():
    assert is_op_reply("t1", "alice", {"t1": "alice"})
    assert not is_op_reply("t1", "bob", {"t1": "alice"})
    assert not is_op_reply("t2", "alice", {"t1": "alice"})


def test_heuristic_not_a_critique_short():
    assert heuristic_critique_category("thanks!") == "not_a_critique"
    assert heuristic_critique_category("Looks solid overall") == "positive_feedback"


def test_heuristic_new_categories():
    assert heuristic_critique_category("tailor this to the JD") == "tailoring"
    assert heuristic_critique_category("drop the todo app project") == "project_selection"
    assert heuristic_critique_category("ATS won't parse your columns") == "ats_formatting"


def test_other_rate_excludes_not_a_critique():
    labeled = [
        {"category": "other"},
        {"category": "metrics"},
        {"category": "not_a_critique"},
        {"category": "not_a_critique"},
    ]
    assert other_rate(labeled) == 0.5


def test_parse_label_batch():
    raw = '[{"idx": 0, "section_targeted": "experience", "category": "metrics"}, {"idx": 1, "section_targeted": "general", "category": "other"}]'
    parsed = _parse_label_batch(raw, 2)
    assert parsed[0]["category"] == "metrics"
    assert parsed[1]["section_targeted"] == "general"


def test_label_critiques_marks_op_replies():
    rec = ThreadRecord(
        thread_id="tid1",
        target_role=TargetRole.SOFTWARE_ENGINEER,
        resume_text="Experience\n• Built things that scaled under load for many users\n",
        context_message="looking for internships",
        critiques=[
            Critique(author="op_user", content="thanks for the feedback everyone"),
            Critique(author="reviewer", content="add metrics to your bullets"),
        ],
    )
    labeled = label_critiques([rec], use_llm=False, op_authors={"tid1": "op_user"})
    assert labeled[0]["category"] == "not_a_critique"
    assert labeled[0]["label_source"] == "op_reply"
    assert labeled[1]["category"] != "not_a_critique" or "metric" in labeled[1]["category"]
