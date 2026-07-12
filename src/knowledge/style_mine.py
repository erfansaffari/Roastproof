"""
Phase 4.6 — mine community style lexicon from critique labels.

  python -m src.knowledge.style_mine --yes
  → data/knowledge/style_lexicon.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from src.generation.prompts import STYLE_MINE_SYSTEM, style_mine_user
from src.llm import MODEL_BULK, complete_json

DEFAULT_LABELS = Path("notebooks/critique_labels.jsonl")
DEFAULT_OUT = Path("data/knowledge/style_lexicon.json")

STYLE_CATEGORIES = frozenset(
    {
        "wording",
        "bullet_quality",
        "redundancy_filler",
        "metrics",
        "other",
    }
)
BATCH_SIZE = 25


class BannedPhrase(BaseModel):
    phrase: str
    example_critique: str = ""
    thread_ids: list[str] = Field(default_factory=list)


class PreferredPattern(BaseModel):
    pattern: str
    example_critique: str = ""
    thread_ids: list[str] = Field(default_factory=list)


class StyleMineBatch(BaseModel):
    banned_phrases: list[BannedPhrase] = Field(default_factory=list)
    preferred_patterns: list[PreferredPattern] = Field(default_factory=list)


def load_labels(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def select_style_critiques(labels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in labels:
        cat = (row.get("category") or "other").lower()
        if cat in {"not_a_critique", "positive_feedback"}:
            continue
        if cat not in STYLE_CATEGORIES and cat != "other":
            # Still include project_selection / experience if they mention fluff-ish wording
            content = (row.get("content") or "").lower()
            if not any(
                w in content
                for w in ("fluff", "vague", "adjective", "quantif", "metric", "filler")
            ):
                continue
        content = (row.get("content") or "").strip()
        if len(content) < 20:
            continue
        out.append(
            {
                "thread_id": str(row.get("thread_id", "")),
                "category": cat,
                "section": row.get("section_targeted") or "general",
                "content": content[:500],
                "original_text": (row.get("original_text") or "")[:200],
            }
        )
    return out


def _norm_phrase(p: str) -> str:
    return " ".join(p.lower().split())


def merge_style_batches(batches: list[StyleMineBatch]) -> dict[str, Any]:
    banned: dict[str, dict[str, Any]] = {}
    preferred: dict[str, dict[str, Any]] = {}

    for batch in batches:
        for b in batch.banned_phrases:
            key = _norm_phrase(b.phrase)
            if not key or len(key) < 2:
                continue
            # Prefer single tokens / short phrases for lint
            if key not in banned:
                banned[key] = {
                    "phrase": b.phrase.strip().lower(),
                    "example_critique": b.example_critique[:240],
                    "supporting_thread_ids": list(b.thread_ids),
                    "frequency": 1,
                }
            else:
                banned[key]["frequency"] += 1
                for tid in b.thread_ids:
                    if tid and tid not in banned[key]["supporting_thread_ids"]:
                        banned[key]["supporting_thread_ids"].append(tid)
                if not banned[key]["example_critique"] and b.example_critique:
                    banned[key]["example_critique"] = b.example_critique[:240]

        for p in batch.preferred_patterns:
            key = _norm_phrase(p.pattern)
            if not key or len(key) < 8:
                continue
            if key not in preferred:
                preferred[key] = {
                    "pattern": p.pattern.strip(),
                    "example_critique": p.example_critique[:240],
                    "supporting_thread_ids": list(p.thread_ids),
                    "frequency": 1,
                }
            else:
                preferred[key]["frequency"] += 1
                for tid in p.thread_ids:
                    if tid and tid not in preferred[key]["supporting_thread_ids"]:
                        preferred[key]["supporting_thread_ids"].append(tid)

    banned_list = sorted(banned.values(), key=lambda x: -x["frequency"])
    preferred_list = sorted(preferred.values(), key=lambda x: -x["frequency"])
    return {
        "meta": {
            "n_banned": len(banned_list),
            "n_preferred": len(preferred_list),
            "model": MODEL_BULK,
        },
        "banned_phrases": banned_list,
        "preferred_patterns": preferred_list,
    }


def mine_style_lexicon(
    critiques: list[dict[str, Any]],
    *,
    batch_size: int = BATCH_SIZE,
    phase: str = "phase4.6-style-mine",
) -> dict[str, Any]:
    batches_out: list[StyleMineBatch] = []
    for i in range(0, len(critiques), batch_size):
        chunk = critiques[i : i + batch_size]
        payload = json.dumps(chunk, indent=2)
        result = complete_json(
            prompt=style_mine_user(payload),
            model=MODEL_BULK,
            phase=phase,
            schema=StyleMineBatch,
            system=STYLE_MINE_SYSTEM,
            max_tokens=4096,
        )
        batches_out.append(result)
        print(f"  style batch {i // batch_size + 1}: "
              f"+{len(result.banned_phrases)} banned, "
              f"+{len(result.preferred_patterns)} preferred")
    return merge_style_batches(batches_out)


def write_lexicon(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mine style lexicon from critiques")
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Required for runs with >100 API calls (G3)",
    )
    args = parser.parse_args(argv)

    labels = load_labels(args.labels)
    critiques = select_style_critiques(labels)
    n_calls = (len(critiques) + args.batch_size - 1) // args.batch_size
    est_tokens = n_calls * 2500
    print(f"Style critiques: {len(critiques)} → ~{n_calls} API calls "
          f"(est ~{est_tokens} tokens)")
    if n_calls > 100 and not args.yes:
        print("Refusing: >100 API calls without --yes (G3).", file=sys.stderr)
        return 2
    if n_calls > 20 and not args.yes:
        print("Pass --yes to proceed with this mining run.", file=sys.stderr)
        return 2

    # Always require --yes for live mining (cost control)
    if not args.yes:
        print("Pass --yes to run style mining.", file=sys.stderr)
        return 2

    payload = mine_style_lexicon(critiques, batch_size=args.batch_size)
    write_lexicon(payload, args.out)
    print(f"Wrote {args.out}: {payload['meta']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
