"""
Phase 4.5/4.6 — pre-generation elicitation (gpt-4o-mini).
"""

from __future__ import annotations

import json
from pathlib import Path

from src.generation.prompts import elicit_system, elicit_user
from src.llm import MODEL_BULK, complete_json
from src.schemas import ElicitationResult, Intake


def elicit_questions(intake: Intake, *, phase: str = "phase4.5-elicit") -> ElicitationResult:
    """Cheap bulk-model call → structured questions."""
    return complete_json(
        prompt=elicit_user(intake),
        model=MODEL_BULK,
        phase=phase,
        schema=ElicitationResult,
        system=elicit_system(intake),
        max_tokens=2048,
    )


def write_questions(result: ElicitationResult, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "questions": [q.model_dump() for q in result.questions],
                "hint": (
                    "Copy unanswered ids into your intake YAML under `answers:` "
                    "as a map of id → answer string, then re-run the pipeline."
                ),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path
