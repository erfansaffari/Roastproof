"""
Phase 4.6 — mine before→after rewrite pairs from critiques.

  python -m src.knowledge.rewrite_mine --yes
  → data/knowledge/rewrite_examples.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from src.generation.prompts import REWRITE_MINE_SYSTEM, rewrite_mine_user
from src.llm import MODEL_BULK, complete_json
from src.knowledge.style_mine import load_labels

DEFAULT_LABELS = Path("notebooks/critique_labels.jsonl")
DEFAULT_OUT = Path("data/knowledge/rewrite_examples.json")
BATCH_SIZE = 20

QUOTE_RE = re.compile(r'["""\u201c\u201d]([^"""\u201c\u201d]{12,200})["""\u201c\u201d]')


class RewritePair(BaseModel):
    before: str
    critique_verbatim: str
    after: str
    section: str = "general"
    thread_id: str = ""


class RewriteMineBatch(BaseModel):
    pairs: list[RewritePair] = Field(default_factory=list)


def select_rewrite_candidates(labels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Prefer rows with original_text or inline quotes in content."""
    out: list[dict[str, Any]] = []
    for row in labels:
        cat = (row.get("category") or "").lower()
        if cat in {"not_a_critique", "positive_feedback"}:
            continue
        content = (row.get("content") or "").strip()
        if len(content) < 30:
            continue
        original = (row.get("original_text") or "").strip()
        quotes = QUOTE_RE.findall(content)
        if not original and not quotes:
            # Still include wording/metrics critiques — model may extract targets
            if cat not in {"wording", "bullet_quality", "metrics", "redundancy_filler"}:
                continue
        out.append(
            {
                "thread_id": str(row.get("thread_id", "")),
                "category": cat,
                "section": row.get("section_targeted") or "general",
                "content": content[:600],
                "original_text": original[:300],
                "inline_quotes": quotes[:3],
            }
        )
    return out


def merge_pairs(batches: list[RewriteMineBatch]) -> dict[str, Any]:
    seen: set[str] = set()
    pairs: list[dict[str, Any]] = []
    for batch in batches:
        for p in batch.pairs:
            before = (p.before or "").strip()
            after = (p.after or "").strip()
            critique = (p.critique_verbatim or "").strip()
            if len(before) < 12 or len(after) < 12 or len(critique) < 12:
                continue
            # after must not invent numbers not in before/critique
            key = before.lower()[:80]
            if key in seen:
                continue
            seen.add(key)
            pairs.append(
                {
                    "before": before,
                    "critique_verbatim": critique[:400],
                    "after": after,
                    "section": p.section or "general",
                    "thread_id": p.thread_id,
                    "supporting_thread_ids": [p.thread_id] if p.thread_id else [],
                }
            )
    return {
        "meta": {"n_pairs": len(pairs), "model": MODEL_BULK},
        "pairs": pairs,
    }


def format_pairs_for_prompt(pairs: list[dict[str, Any]], *, k: int = 4, section: str | None = None) -> str:
    """Select top-k pairs (optionally section-filtered) as few-shot text."""
    ACTION = re.compile(
        r"^(Built|Designed|Implemented|Developed|Created|Refactored|Reduced|"
        r"Shipped|Wrote|Added|Fixed|Migrated|Deployed|Owned|Led|Improved|"
        r"Triaged|Packaged|Architected|Engineered)",
        re.I,
    )
    ranked = pairs
    if section:
        sec = section.lower()
        preferred = [p for p in pairs if (p.get("section") or "").lower() == sec]
        other = [p for p in pairs if p not in preferred]
        ranked = preferred + other

    # Prefer afters that look like resume bullets
    bulletish = [p for p in ranked if ACTION.match((p.get("after") or "").strip())]
    if len(bulletish) >= k:
        ranked = bulletish
    lines: list[str] = []
    for p in ranked[:k]:
        lines.append(f'Weak: "{p["before"]}"')
        lines.append(f'Critique: {p["critique_verbatim"][:180]}')
        lines.append(f'Strong: "{p["after"]}"')
        lines.append("")
    return "\n".join(lines).strip() or "(no mined rewrite examples)"


def mine_rewrite_examples(
    critiques: list[dict[str, Any]],
    *,
    batch_size: int = BATCH_SIZE,
    phase: str = "phase4.6-rewrite-mine",
) -> dict[str, Any]:
    batches_out: list[RewriteMineBatch] = []
    for i in range(0, len(critiques), batch_size):
        chunk = critiques[i : i + batch_size]
        result = complete_json(
            prompt=rewrite_mine_user(json.dumps(chunk, indent=2)),
            model=MODEL_BULK,
            phase=phase,
            schema=RewriteMineBatch,
            system=REWRITE_MINE_SYSTEM,
            max_tokens=4096,
        )
        batches_out.append(result)
        print(f"  rewrite batch {i // batch_size + 1}: +{len(result.pairs)} pairs")
    return merge_pairs(batches_out)


def write_examples(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_rewrite_examples(path: Path = DEFAULT_OUT) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("pairs") or [])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mine rewrite examples from critiques")
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args(argv)

    labels = load_labels(args.labels)
    critiques = select_rewrite_candidates(labels)
    # Cap candidates for cost — prioritize those with quotes
    with_quote = [c for c in critiques if c.get("original_text") or c.get("inline_quotes")]
    without = [c for c in critiques if c not in with_quote]
    critiques = (with_quote + without)[:200]

    n_calls = (len(critiques) + args.batch_size - 1) // args.batch_size
    print(f"Rewrite candidates: {len(critiques)} → ~{n_calls} API calls")
    if not args.yes:
        print("Pass --yes to run rewrite mining.", file=sys.stderr)
        return 2

    payload = mine_rewrite_examples(critiques, batch_size=args.batch_size)
    write_examples(payload, args.out)
    print(f"Wrote {args.out}: {payload['meta']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
