"""
Phase 4.5/4.7 — pre-generation elicitation with persistent Q&A memory.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.generation.prompts import (
    elicit_system,
    elicit_user,
    expand_elicit_system,
    expand_elicit_user,
)
from src.generation.intake_coverage import (
    autofill_covered_pending,
    filter_questions_covered_by_intake,
)
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


def _elicit_critiques_block(intake: Intake, k: int = 6) -> str:
    """Retrieve experience/project critiques so elicitation is corpus-grounded."""
    try:
        from src.knowledge.retrieve import retrieve
    except Exception:
        return "(no critiques retrieved)"

    profile = intake.to_applicant_profile()
    query_parts = [intake.target_role, intake.profile_summary or ""]
    for exp in intake.experience or []:
        query_parts.append(f"{exp.company} {exp.title} {(exp.description or '')[:160]}")
    for p in intake.projects or []:
        query_parts.append(f"{p.name} {p.technologies} {(p.description or '')[:160]}")
    query = " ".join(query_parts)

    points = []
    seen: set[str] = set()
    for section in ("experience", "projects", "general"):
        try:
            batch = retrieve(profile, section, query, k=k)
        except Exception:
            continue
        for pt in batch:
            if not pt.id or pt.id in seen:
                continue
            seen.add(pt.id)
            points.append(pt)

    if not points:
        return "(no critiques retrieved)"

    lines: list[str] = []
    for i, p in enumerate(points[:12], 1):
        score = f"{p.score:.3f}" if p.score is not None else "?"
        lines.append(
            f"{i}. id={p.id} [{p.section}/{p.category}] (score={score}) {p.issue}"
        )
    return "\n".join(lines)


def elicit_questions(
    intake: Intake,
    store: QAStore,
    *,
    phase: str = "phase4.7-elicit",
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    intake_hash: str = "",
) -> tuple[ElicitationResult, QAStore, dict]:
    """
    Cheap bulk-model call → structured questions, merged into the QA sidecar.

    Returns (raw_llm_result, updated_store, meta) where meta has:
      round, new_count, surviving_count, converged, stop_reason, counts
    """
    # Auto-answer pending questions whose facts are already in the intake text
    store = autofill_covered_pending(store, intake)

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
    critiques_block = _elicit_critiques_block(intake)
    raw = complete_json(
        prompt=elicit_user(
            intake,
            history_block=history,
            critiques_block=critiques_block,
        ),
        model=MODEL_BULK,
        phase=phase,
        schema=ElicitationResult,
        system=elicit_system(intake, next_round=next_round),
        max_tokens=2048,
        temperature=0,
    )

    # Normalize impact defaults
    for q in raw.questions:
        if not q.impact:
            q.impact = "high"
        q.impact = q.impact.lower()

    after_impact = filter_by_round_impact(raw.questions, next_round=next_round)
    # Drop questions already answered by intake descriptions (deterministic)
    after_coverage = filter_questions_covered_by_intake(after_impact, intake)
    dropped_covered = len(after_impact) - len(after_coverage)
    surviving = semantic_dedup_questions(after_coverage, store.questions)
    updated = append_new_questions(store, surviving, round_num=next_round)
    if intake_hash:
        updated = updated.model_copy(update={"intake_hash": intake_hash})

    stop, reason = should_stop_elicitation(
        updated,
        model_complete=bool(raw.complete),
        new_surviving=len(surviving),
        max_rounds=max_rounds,
        dropped_covered=dropped_covered,
        next_round=next_round,
    )
    # Round 1: do NOT latch on empty surviving just because filters ate everything.
    # Only converge on round 1 when model said complete AND we dropped nothing.
    if next_round <= 1 and len(surviving) == 0 and counts(updated).get("pending", 0) == 0:
        if raw.complete and dropped_covered == 0:
            stop, reason = True, raw.completion_reason or "model marked complete"
        else:
            stop, reason = False, ""
    elif raw.complete and len(surviving) == 0 and next_round > 1:
        stop, reason = True, raw.completion_reason or "model marked complete"
    elif len(surviving) == 0 and counts(updated).get("pending", 0) == 0 and next_round > 1:
        stop, reason = True, reason or "intake already covers material gaps"

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
        "dropped_covered": dropped_covered,
        "critiques_injected": critiques_block != "(no critiques retrieved)",
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


def _fallback_expand_questions(
    intake: Intake,
    *,
    unused_facts: list[str],
    thin_entries: list[str],
) -> list:
    """Deterministic questions when the LLM returns nothing but the page is thin."""
    from src.schemas import ElicitationQuestion

    qs: list[ElicitationQuestion] = []
    # Prefer unused-fact prompts
    for fact in unused_facts[:3]:
        qs.append(
            ElicitationQuestion(
                id="",
                topic="expand_content",
                impact="high",
                question=(
                    f"Can you add more concrete detail (tools, scope, outcome) for: {fact} "
                    f"— enough for a full resume bullet?"
                ),
                relates_to=fact[:120],
            )
        )
    if qs:
        return qs
    # Fall back to per-entry asks
    for exp in intake.experience[:2]:
        qs.append(
            ElicitationQuestion(
                id="",
                topic="expand_content",
                impact="high",
                question=(
                    f"Your {exp.company} role still leaves page whitespace — describe any "
                    f"testing, monitoring, on-call, code review, or performance work you did "
                    f"there that is not already in the intake."
                ),
                relates_to=exp.company,
            )
        )
    for proj in intake.projects[:2]:
        qs.append(
            ElicitationQuestion(
                id="",
                topic="expand_content",
                impact="high",
                question=(
                    f"For project {proj.name}: any users/installs, deployment, benchmarks, "
                    f"or design tradeoffs you can quantify that are missing from the intake?"
                ),
                relates_to=proj.name,
            )
        )
    if thin_entries and not qs:
        qs.append(
            ElicitationQuestion(
                id="",
                topic="expand_content",
                impact="high",
                question=(
                    "The resume is under one full page. What additional shipped work, "
                    "metrics, or ownership details can you add for your strongest role?"
                ),
                relates_to=thin_entries[0][:80],
            )
        )
    return qs[:4]


def elicit_expansion_questions(
    intake: Intake,
    store: QAStore,
    *,
    fill_ratio: float,
    fill_target: float,
    thin_entries: list[str],
    unused_facts: list[str] | None = None,
    phase: str = "phase4.8-expand-elicit",
) -> tuple[ElicitationResult, QAStore, dict]:
    """
    Ask expand_content questions when the page is under-filled.

    Works even if the sidecar is already metric-converged. If the LLM returns
    zero questions while fill < target, inject deterministic fallback questions.
    """
    unused_facts = list(unused_facts or [])
    history = format_history_block(store)
    fill_report = (
        f"Page fill {fill_ratio:.0%} (target ≥{fill_target:.0%}). "
        "Whitespace remains below Technical Skills — need more grounded bullets."
    )
    raw = complete_json(
        prompt=expand_elicit_user(
            intake=intake,
            history_block=history,
            fill_report=fill_report,
            thin_entries=thin_entries,
            unused_facts=unused_facts,
        ),
        model=MODEL_BULK,
        phase=phase,
        schema=ElicitationResult,
        system=expand_elicit_system(intake),
        max_tokens=2048,
        temperature=0,
    )

    for q in raw.questions:
        q.topic = "expand_content"
        if not q.impact:
            q.impact = "high"
        q.impact = q.impact.lower()

    surviving = semantic_dedup_questions(raw.questions, store.questions)

    # Force questions when still under-filled — do not let the model "complete" away
    if not surviving and fill_ratio < fill_target:
        fallback = _fallback_expand_questions(
            intake, unused_facts=unused_facts, thin_entries=thin_entries
        )
        surviving = semantic_dedup_questions(fallback, store.questions)
        raw = ElicitationResult(
            questions=surviving,
            complete=False,
            completion_reason="fallback questions injected because page under-filled",
        )

    next_round = max(store.round, 1) + 1
    updated = append_new_questions(store, surviving, round_num=next_round)

    if surviving:
        updated = updated.model_copy(update={"converged": False})
    elif raw.complete and fill_ratio >= fill_target:
        updated = updated.model_copy(update={"converged": store.converged})

    meta = {
        "round": updated.round,
        "new_count": len(raw.questions),
        "surviving_count": len(surviving),
        "converged": updated.converged,
        "counts": counts(updated),
        "completion_reason": raw.completion_reason or "",
    }
    return raw, updated, meta
