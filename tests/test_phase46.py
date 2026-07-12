"""Unit tests for Phase 4.6 prompt library + corpus-derived helpers."""

from __future__ import annotations

import json
from pathlib import Path

from src.generation.fluff import banned_phrase_set, load_style_lexicon
from src.generation.project_eval import drop_ungrounded, project_eval_to_suggestions
from src.generation.prompts import (
    DATA_WINS_CLAUSE,
    assert_prompt_invariants,
    elicit_system,
    generator_system,
    project_eval_system,
    resolve_role_profile,
)
from src.knowledge.rewrite_mine import format_pairs_for_prompt, merge_pairs
from src.knowledge.style_mine import StyleMineBatch, BannedPhrase, merge_style_batches
from src.schemas import FieldGap, Intake, ProjectEvalResult, ProjectVerdict


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


def test_resolve_role_profiles():
    assert resolve_role_profile("Machine Learning Engineer").key == "ml_engineer"
    assert resolve_role_profile("Frontend Engineer").key == "frontend"
    assert resolve_role_profile("Backend Engineer").key == "backend"
    assert resolve_role_profile("AI Engineer").key == "ai_engineer"
    assert resolve_role_profile("Software Engineer").key == "software_engineer"


def test_generator_prompt_invariants():
    sys = generator_system(_intake())
    missing = assert_prompt_invariants(sys)
    assert missing == [], missing
    assert DATA_WINS_CLAUSE.split(",")[0][:20].lower() in sys.lower() or "data wins" in sys.lower()
    assert "200 resumes" in sys.lower() or "30 seconds" in sys.lower()


def test_elicit_and_project_eval_prompt_invariants():
    intake = _intake(target_role="Machine Learning Engineer")
    for text in (elicit_system(intake), project_eval_system(intake)):
        missing = assert_prompt_invariants(text)
        assert missing == [], (missing, text[:200])
        assert "machine learning" in text.lower() or "ml" in text.lower()


def test_style_lexicon_loads_or_falls_back():
    banned = banned_phrase_set()
    assert "seamless" in banned
    assert "robust" in banned
    # Must not ban generic resume nouns from noisy mining
    assert "development" not in banned or Path("data/knowledge/style_lexicon.json").exists()
    # After filter, development should not be in promote set
    assert "development" not in banned


def test_merge_style_batches_dedupes():
    b1 = StyleMineBatch(
        banned_phrases=[
            BannedPhrase(phrase="seamless", example_critique="x", thread_ids=["1"]),
            BannedPhrase(phrase="Seamless", example_critique="y", thread_ids=["2"]),
        ]
    )
    out = merge_style_batches([b1])
    phrases = [p["phrase"] for p in out["banned_phrases"]]
    assert phrases.count("seamless") + sum(1 for p in phrases if p.lower() == "seamless") >= 1
    seamless = next(p for p in out["banned_phrases"] if p["phrase"].lower() == "seamless")
    assert seamless["frequency"] == 2
    assert set(seamless["supporting_thread_ids"]) == {"1", "2"}


def test_format_pairs_prefers_action_after():
    pairs = [
        {
            "before": "demonstrated agility in development with many tools",
            "critique_verbatim": "nonsense",
            "after": "Remove this fluff statement entirely from the resume",
            "section": "experience",
        },
        {
            "before": "built a robust system to ensure integrity of data",
            "critique_verbatim": "fluff adjectives",
            "after": "Designed tenant-scoped RBAC so each school's data stays partitioned",
            "section": "experience",
        },
    ]
    text = format_pairs_for_prompt(pairs, k=1)
    assert "Designed tenant-scoped" in text
    assert "Remove this fluff" not in text


def test_drop_ungrounded_project_eval():
    raw = ProjectEvalResult(
        projects=[
            ProjectVerdict(
                name="Good",
                verdict="strengthen",
                rationale="needs metrics",
                evidence_ids=["crit-1"],
            ),
            ProjectVerdict(
                name="Bad",
                verdict="replace",
                rationale="made up",
                evidence_ids=["hallucinated-id"],
            ),
        ],
        field_gaps=[
            FieldGap(gap="systems project", evidence_ids=["crit-1"]),
            FieldGap(gap="invented gap", evidence_ids=["nope"]),
        ],
    )
    cleaned = drop_ungrounded(raw, {"crit-1"})
    names = [p.name for p in cleaned.projects]
    assert names == ["Good"]
    assert len(cleaned.field_gaps) == 1
    sugs = project_eval_to_suggestions(cleaned)
    assert all(s.type == "project_evaluation" for s in sugs)
    assert any("Good" in s.detail for s in sugs)


def test_lexicon_file_if_present():
    path = Path("data/knowledge/style_lexicon.json")
    if not path.is_file():
        return
    data = load_style_lexicon(str(path))
    assert "banned_phrases" in data
    assert data["meta"].get("n_banned", 0) >= 1 or len(data["banned_phrases"]) >= 1
