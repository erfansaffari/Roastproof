"""
Phase 4.5/4.7 — pre-generation elicitation with persistent Q&A memory.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.generation.prompts import elicit_system, elicit_user
from src.generation.qa_store import (
    DEFAULT_MAX_ROUNDS,
    append_new_questions,
    counts,
    filter_by_round_impact,
    format_history_block,
    semantic_dedup_questions,
    should_stop_elicitation,
)
from src.llm import MODEL_BULK, complete_json
from src.schemas import ElicitationResult, Intake, QAStore


def elicit_questions(
    intake: Intake,
    store: QAStore,
    *,
    phase: str = "phase4.7-elicit",
    max_rounds: int = DEFAULT_MAX_ROUNDS,
) -> tuple[ElicitationResult, QAStore, dict]:
    """
    Cheap bulk-model call → structured questions, merged into the QA sidecar.

    Returns (raw_llm_result, updated_store, meta) where meta has:
      round, new_count, surviving_count, converged, stop_reason, counts
    """
    if store.converged:
        empty = ElicitationResult(
            questions=[],
            complete=True,
            completion_reason="already converged",
        )
        meta = {
            "round": store.round,
            "new_count": 0,
            "surviving_count": 0,
            "converged": True,
            "stop_reason": "already converged",
            "counts": counts(store),
            "skipped_llm": True,
        }
        return empty, store, meta

    next_round = store.round + 1
    history = format_history_block(store)
    raw = complete_json(
        prompt=elicit_user(intake, history_block=history),
        model=MODEL_BULK,
        phase=phase,
        schema=ElicitationResult,
        system=elicit_system(intake),
        max_tokens=2048,
        temperature=0,
    )

    # Normalize impact defaults
    for q in raw.questions:
        if not q.impact:
            q.impact = "high"
        q.impact = q.impact.lower()

    after_impact = filter_by_round_impact(raw.questions, next_round=next_round)
    surviving = semantic_dedup_questions(after_impact, store.questions)
    updated = append_new_questions(store, surviving, round_num=next_round)

    stop, reason = should_stop_elicitation(
        updated,
        model_complete=bool(raw.complete),
        new_surviving=len(surviving),
        max_rounds=max_rounds,
    )
    # Also stop if model said complete even with questions we dropped as dupes
    if raw.complete and len(surviving) == 0:
        stop, reason = True, raw.completion_reason or "model marked complete"
    if stop:
        updated = updated.model_copy(update={"converged": True})

    meta = {
        "round": updated.round,
        "new_count": len(raw.questions),
        "surviving_count": len(surviving),
        "converged": updated.converged,
        "stop_reason": reason if stop else "",
        "counts": counts(updated),
        "skipped_llm": False,
        "completion_reason": raw.completion_reason or "",
    }
    return raw, updated, meta


def write_questions(store: QAStore, path: Path, *, hint: str | None = None) -> Path:
    """Write human-readable questions.json snapshot for the out dir."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = [q for q in store.questions if q.status == "pending"]
    default_hint = (
        f"Edit answers in the sidecar next to your intake "
        f"(*.qa.yaml). Leave null to keep pending; set to 'skip' to decline. "
        f"Then re-run the pipeline. Converged={store.converged}."
    )
    path.write_text(
        json.dumps(
            {
                "round": store.round,
                "converged": store.converged,
                "pending": [q.model_dump() for q in pending],
                "all_questions": [q.model_dump() for q in store.questions],
                "hint": hint or default_hint,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path
