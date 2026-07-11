"""Phase 4 — intake loader (YAML/JSON → Intake)."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from src.schemas import Intake

SUPPORTED_SUFFIXES = {".yaml", ".yml", ".json"}


def load_intake(path: Path | str) -> Intake:
    """Load and validate an intake file into the Intake model."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Intake file not found: {path}")
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError(
            f"Unsupported intake format {suffix!r}; use one of {sorted(SUPPORTED_SUFFIXES)}"
        )
    text = path.read_text(encoding="utf-8")
    if suffix == ".json":
        data = json.loads(text)
    else:
        data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError(f"Intake root must be a mapping, got {type(data).__name__}")
    return Intake.model_validate(data)
