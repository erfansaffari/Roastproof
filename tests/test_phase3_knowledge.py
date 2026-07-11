"""Unit tests for Phase 3 knowledge-base helpers (no live LLM / Chroma)."""

from src.knowledge.rulebook import verify_rule_evidence
from src.knowledge.retrieve import (
    EXCLUDED_RETRIEVAL_CATEGORIES,
    format_for_prompt,
    normalize_agreement,
    profile_match,
    rerank_score,
    resolve_roles,
)
from src.knowledge.vectorstore import build_composite, explode_thread
from src.schemas import Critique, CritiquePoint, Rule, TargetRole, ThreadRecord


def test_build_composite_format():
    s = build_composite(
        target_role="Software Engineer",
        profile_summary="2B CS intern",
        section="experience",
        original_text="Built an API",
        issue="add metrics",
        suggestion="quantify impact",
    )
    assert s.startswith("[Software Engineer] [2B CS intern] [experience]")
    assert "resume text: Built an API" in s
    assert "critique: add metrics → quantify impact" in s


def test_build_composite_no_suggestion():
    s = build_composite(
        "Software Engineer", "student", "general", None, "too cluttered", ""
    )
    assert "resume text: (no quote)" in s
    assert "critique: too cluttered" in s
    assert "→" not in s.split("critique:")[1]


def test_explode_skips_empty_and_not_a_critique():
    rec = ThreadRecord(
        thread_id="t1",
        target_role=TargetRole.SOFTWARE_ENGINEER,
        resume_text="Experience\n• Built things under load for users\n",
        applicant_profile="2B CS",
        context_message="looking for internships",
        critiques=[
            Critique(author="a", content=""),
            Critique(author="a", content="thanks!"),
            Critique(author="b", content="add metrics to bullets"),
        ],
    )
    labels = {
        ("t1", "thanks!"): {"category": "not_a_critique", "section_targeted": "general"},
        ("t1", "add metrics to bullets"): {
            "category": "metrics",
            "section_targeted": "experience",
        },
    }
    points = explode_thread(rec, labels)
    assert len(points) == 1
    assert points[0].issue == "add metrics to bullets"
    assert points[0].section == "experience"
    assert points[0].category == "metrics"
    assert "add metrics to bullets" in points[0].composite


def test_profile_match_unknown_soft():
    assert profile_match("year_2", True, "year_2", True) == 1.0
    soft = profile_match("unknown", True, "year_2", True)
    assert abs(soft - (0.7 * 0.5 + 0.3 * 1.0)) < 1e-9
    mismatch = profile_match("year_1", False, "year_4", False)
    assert abs(mismatch - (0.7 * 0.0 + 0.3 * 1.0)) < 1e-9
    soft_doc = profile_match("year_2", True, "unknown", True)
    assert soft_doc == soft


def test_rerank_score_weights():
    s = rerank_score(1.0, 1.0, 3)
    assert abs(s - 1.0) < 1e-9
    # Viral agree=9 must not beat agree=3
    assert rerank_score(0.5, 0.5, 9) == rerank_score(0.5, 0.5, 3)
    s2 = rerank_score(1.0, 0.0, 0)
    assert abs(s2 - 0.7) < 1e-9
    assert normalize_agreement(0) == 0.0
    assert normalize_agreement(3) == 1.0
    assert normalize_agreement(9) == 1.0
    assert normalize_agreement(1) == 1 / 3


def test_resolve_roles_fallback():
    assert resolve_roles("Machine Learning Engineer") == [
        "Machine Learning Engineer",
        "Software Engineer",
    ]
    assert resolve_roles("Software Engineer") == ["Software Engineer"]


def test_excluded_positive_feedback_constant():
    assert "positive_feedback" in EXCLUDED_RETRIEVAL_CATEGORIES


def test_verify_rule_evidence_hallucination_guard():
    rules = [
        Rule(
            category="metrics",
            section="experience",
            applies_to=["swe_intern"],
            statement="Quantify impact with metrics",
            frequency=99,
            evidence_examples=["add numbers"],
            supporting_thread_ids=["real1", "real2", "real3", "real4", "real5", "fake99"],
        ),
        Rule(
            category="formatting",
            section="general",
            applies_to=[],
            statement="Keep to one page",
            frequency=3,
            evidence_examples=["too long"],
            supporting_thread_ids=["real1", "ghost"],
        ),
        Rule(
            category="wording",
            section="general",
            applies_to=[],
            statement="",
            frequency=10,
            evidence_examples=[],
            supporting_thread_ids=["real1"],
        ),
    ]
    corpus = {f"real{i}" for i in range(1, 6)}
    kept, discarded = verify_rule_evidence(rules, corpus, min_frequency=5)
    assert len(kept) == 1
    assert kept[0].frequency == 5.0
    assert "fake99" not in kept[0].supporting_thread_ids
    assert any(d["reason"] == "below_min_frequency" for d in discarded)
    assert any(d["reason"] == "empty_statement" for d in discarded)


def test_format_for_prompt():
    points = [
        CritiquePoint(
            id="a",
            thread_id="t",
            target_role="Software Engineer",
            section="experience",
            issue="add metrics",
            category="metrics",
            score=0.91,
            agreement_signal=2,
        )
    ]
    text = format_for_prompt(points)
    assert "1." in text
    assert "add metrics" in text
    assert "0.910" in text
    assert format_for_prompt([]) == "(no critiques retrieved)"
    long = CritiquePoint(
        id="b",
        thread_id="t2",
        target_role="Software Engineer",
        section="general",
        issue="x" * 200,
        category="other",
        score=0.5,
    )
    clipped = format_for_prompt([long], max_chars=50)
    assert "…" in clipped
    assert "xxxx" in clipped
    assert "x" * 60 not in clipped
