"""
Deterministic banned-fluff lint for generated resume bullets.

Loads community-mined phrases from data/knowledge/style_lexicon.json when
present; falls back to a small seed list otherwise.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from src.schemas import ResumeContent

DEFAULT_LEXICON_PATH = Path("data/knowledge/style_lexicon.json")

# Seed / fallback when lexicon missing or too small (Phase 4.5).
SEED_BANNED_FLUFF: frozenset[str] = frozenset(
    {
        "seamless",
        "robust",
        "effective",
        "enhanced",
        "streamlined",
        "reliable",
        "ensure",
        "ensuring",
        "ensures",
        "optimized",
        "optimised",
        "secure",
        "fast",
        "cutting-edge",
        "state-of-the-art",
        "leveraged",
        "leverage",
        "leveraging",
        "utilize",
        "utilized",
        "utilised",
        "utilizing",
        "utilising",
    }
)

# Back-compat alias
BANNED_FLUFF = SEED_BANNED_FLUFF

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z\-']*")
_MIN_LEXICON_PHRASES = 5


@lru_cache(maxsize=4)
def load_style_lexicon(path_str: str = str(DEFAULT_LEXICON_PATH)) -> dict:
    path = Path(path_str)
    if not path.is_file():
        return {"banned_phrases": [], "preferred_patterns": [], "meta": {"source": "missing"}}
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("meta", {})["source"] = str(path)
    return data


def banned_phrase_set(lexicon_path: Path = DEFAULT_LEXICON_PATH) -> frozenset[str]:
    """Single-token banned set for word-level lint (seed ∪ high-signal mined)."""
    data = load_style_lexicon(str(lexicon_path))
    phrases = data.get("banned_phrases") or []
    mined: set[str] = set()
    # Only promote mined tokens that look like empty adjectives / filler,
    # not arbitrary critique nouns (avoids banning "development", etc.).
    promote = {
        "seamless", "robust", "effective", "enhanced", "streamlined", "reliable",
        "ensure", "ensuring", "ensures", "optimized", "optimised", "secure", "fast",
        "cutting-edge", "state-of-the-art", "leveraged", "leverage", "leveraging",
        "utilize", "utilized", "utilised", "utilizing", "utilising", "fluff",
        "wordy", "hand-wavey", "handwavey", "skillfully", "agility", "synergy",
        "spearheaded", "revolutionize", "revolutionized", "filler",
    }
    for row in phrases:
        phrase = (row.get("phrase") if isinstance(row, dict) else str(row)) or ""
        for tok in _WORD_RE.findall(phrase.lower()):
            if tok in promote or tok in SEED_BANNED_FLUFF:
                mined.add(tok)
    return frozenset(SEED_BANNED_FLUFF | mined)


def banned_multiword_phrases(lexicon_path: Path = DEFAULT_LEXICON_PATH) -> list[str]:
    data = load_style_lexicon(str(lexicon_path))
    out: list[str] = []
    for row in data.get("banned_phrases") or []:
        phrase = (row.get("phrase") if isinstance(row, dict) else str(row)) or ""
        phrase = phrase.strip().lower()
        if " " in phrase and len(phrase) >= 6:
            out.append(phrase)
    return out


def preferred_patterns(lexicon_path: Path = DEFAULT_LEXICON_PATH) -> list[str]:
    data = load_style_lexicon(str(lexicon_path))
    out: list[str] = []
    for row in data.get("preferred_patterns") or []:
        if isinstance(row, dict):
            p = row.get("pattern") or ""
        else:
            p = str(row)
        if p.strip():
            out.append(p.strip())
    return out


def find_fluff_hits(
    text: str,
    banned: Iterable[str] | None = None,
    lexicon_path: Path = DEFAULT_LEXICON_PATH,
) -> list[str]:
    """Return banned tokens/phrases found in text (lowercase)."""
    banned_set = set(b.lower() for b in (banned if banned is not None else banned_phrase_set(lexicon_path)))
    hits: list[str] = []
    seen: set[str] = set()
    lower = (text or "").lower()
    for phrase in banned_multiword_phrases(lexicon_path):
        if phrase in lower and phrase not in seen:
            hits.append(phrase)
            seen.add(phrase)
    for m in _WORD_RE.finditer(text or ""):
        w = m.group(0).lower()
        if w in banned_set and w not in seen:
            hits.append(w)
            seen.add(w)
    return hits


def lint_resume_fluff(
    content: ResumeContent,
    lexicon_path: Path = DEFAULT_LEXICON_PATH,
) -> list[str]:
    """Scan all experience/project bullets for banned fluff."""
    violations: list[str] = []
    banned = banned_phrase_set(lexicon_path)
    for i, entry in enumerate(content.experience or []):
        for j, bullet in enumerate(entry.get("bullets") or []):
            hits = find_fluff_hits(bullet, banned=banned, lexicon_path=lexicon_path)
            if hits:
                violations.append(
                    f"experience[{i}].bullets[{j}] contains fluff {hits}: {bullet!r}"
                )
    for i, entry in enumerate(content.projects or []):
        for j, bullet in enumerate(entry.get("bullets") or []):
            hits = find_fluff_hits(bullet, banned=banned, lexicon_path=lexicon_path)
            if hits:
                violations.append(
                    f"projects[{i}].bullets[{j}] contains fluff {hits}: {bullet!r}"
                )
    return violations


def fluff_retry_instruction(
    violations: list[str],
    lexicon_path: Path = DEFAULT_LEXICON_PATH,
) -> str:
    """Append to generator prompt for one fluff-lint retry."""
    banned = sorted(banned_phrase_set(lexicon_path))[:12]
    joined = "\n".join(f"- {v}" for v in violations[:12])
    return (
        "FLUFF LINT FAILED — rewrite the flagged bullets. Remove empty adjectives "
        f"({', '.join(banned)}, …). Replace with concrete actions, "
        "scope, and tools. Do NOT invent metrics. Violations:\n"
        f"{joined}"
    )
