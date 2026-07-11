"""
Phase 3 — consensus rulebook via map/reduce LLM synthesis.

Pass 1 (map): batch threads → candidate rules with supporting thread_ids.
Pass 2 (reduce): merge/dedupe into canonical Rule objects.
Post-check: discard rules whose claimed thread_ids are not in the corpus;
recount frequency from surviving evidence (hallucination guard).
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from src import llm
from src.knowledge.norms import load_threads
from src.schemas import Rule, ThreadRecord

DEFAULT_BATCH_SIZE = 25
DEFAULT_MIN_FREQUENCY = 10
# Pilot corpora (< ~200 threads) rarely hit frequency≥10 for 30+ rules.
# Auto-lower when corpus is small unless --min-frequency is set explicitly.
PILOT_MIN_FREQUENCY = 5

RULE_CATEGORIES = [
    "bullet_quality",
    "metrics",
    "skills",
    "section_order",
    "formatting",
    "ats_formatting",
    "education",
    "experience",
    "projects",
    "project_selection",
    "wording",
    "length",
    "contact",
    "links_portfolio",
    "tailoring",
    "redundancy_filler",
]

RULE_SECTIONS = [
    "education",
    "experience",
    "projects",
    "skills",
    "contact",
    "formatting",
    "general",
]


class CandidateRule(BaseModel):
    category: str
    section: str
    applies_to: list[str] = Field(default_factory=list)
    statement: str
    supporting_thread_ids: list[str] = Field(default_factory=list)
    evidence_examples: list[str] = Field(default_factory=list)


class CandidateRuleBatch(BaseModel):
    rules: list[CandidateRule] = Field(default_factory=list)


class RuleBook(BaseModel):
    rules: list[Rule] = Field(default_factory=list)


MAP_SYSTEM = (
    "You extract consensus resume-critique rules from Discord threads. "
    "Respond with ONLY a JSON object: {\"rules\": [...]}. "
    "Each rule needs: category, section, applies_to (role/profile tags), "
    "statement (imperative community advice), supporting_thread_ids "
    "(only IDs from the batch that clearly support the rule), "
    "evidence_examples (1–3 short quotes from critiques). "
    f"category must be one of: {', '.join(RULE_CATEGORIES)}. "
    f"section must be one of: {', '.join(RULE_SECTIONS)}. "
    "Do NOT invent thread IDs. "
    "Aim for 15–25 DISTINCT rules per batch covering different categories "
    "(metrics, wording, formatting, skills, projects, education, length, "
    "section_order, tailoring, etc.). Skip pure thank-yous and off-topic chat."
)

REDUCE_SYSTEM = (
    "You merge candidate resume-critique rules into a canonical rulebook. "
    "Respond with ONLY a JSON object: {\"rules\": [...]}. "
    "ONLY merge near-exact duplicates (same advice, different wording). "
    "Prefer KEEPING distinct rules — target 30–60 final rules when possible. "
    "Union supporting_thread_ids; keep the 2–3 clearest evidence_examples; "
    "keep the strongest statement wording. "
    "Drop rules that are too vague or contradictory. "
    f"category ∈ {{{', '.join(RULE_CATEGORIES)}}}; "
    f"section ∈ {{{', '.join(RULE_SECTIONS)}}}."
)


def _thread_blob(rec: ThreadRecord, max_critiques: int = 8) -> str:
    role = rec.target_role.value if hasattr(rec.target_role, "value") else str(rec.target_role)
    critiques = []
    for c in rec.critiques[:max_critiques]:
        text = (c.content or "").replace("\n", " ").strip()
        if len(text) < 12:
            continue
        if len(text) > 280:
            text = text[:280] + "…"
        critiques.append(f"  - {text}")
    if not critiques:
        return ""
    profile = (rec.applicant_profile or "")[:120]
    ctx = (rec.context_message or "")[:160]
    return (
        f"thread_id={rec.thread_id}\n"
        f"role={role}\n"
        f"profile={profile}\n"
        f"context={ctx}\n"
        f"critiques:\n" + "\n".join(critiques)
    )


def batch_threads(records: list[ThreadRecord], batch_size: int) -> list[list[ThreadRecord]]:
    return [records[i : i + batch_size] for i in range(0, len(records), batch_size)]


def _parse_candidate_batch(raw: str) -> list[CandidateRule]:
    text = llm._extract_json(raw)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0:
            start = raw.find("[")
            end = raw.rfind("]")
            if start < 0:
                return []
            data = json.loads(raw[start : end + 1])
        else:
            data = json.loads(raw[start : end + 1])

    if isinstance(data, list):
        data = {"rules": data}

    # Happy path
    if isinstance(data, dict) and "rules" in data and isinstance(data["rules"], list):
        # If items look like CandidateRule dicts
        if data["rules"] and isinstance(data["rules"][0], dict) and "statement" in data["rules"][0]:
            try:
                return CandidateRuleBatch.model_validate(data).rules
            except Exception:
                pass

    # Salvage alternate shapes the model sometimes invents
    salvaged: list[CandidateRule] = []
    if isinstance(data, dict):
        # {"consensus_rules": [{"category": "...", "rules": ["stmt", ...]}, ...]}
        cr = data.get("consensus_rules") or data.get("candidates") or data.get("rulebook")
        if isinstance(cr, list):
            for block in cr:
                if not isinstance(block, dict):
                    continue
                cat = _normalize_category(str(block.get("category", "other")))
                sec = _normalize_section(str(block.get("section", "general")))
                applies = block.get("applies_to") or []
                tids = block.get("supporting_thread_ids") or block.get("thread_ids") or []
                examples = block.get("evidence_examples") or []
                stmts = block.get("rules") or block.get("statements") or []
                if isinstance(stmts, str):
                    stmts = [stmts]
                if "statement" in block:
                    stmts = list(stmts) + [block["statement"]]
                for stmt in stmts:
                    if not isinstance(stmt, str) or len(stmt.strip()) < 8:
                        continue
                    salvaged.append(
                        CandidateRule(
                            category=cat,
                            section=sec,
                            applies_to=list(applies) if isinstance(applies, list) else [],
                            statement=stmt.strip(),
                            supporting_thread_ids=[str(t) for t in tids],
                            evidence_examples=[str(e) for e in examples[:3]],
                        )
                    )
        elif isinstance(cr, dict):
            # {"consensus_rules": {"general_tips": ["...", ...]}}
            for cat_key, stmts in cr.items():
                if not isinstance(stmts, list):
                    continue
                cat = _normalize_category(str(cat_key))
                for stmt in stmts:
                    if not isinstance(stmt, str) or len(stmt.strip()) < 8:
                        continue
                    salvaged.append(
                        CandidateRule(
                            category=cat,
                            section="general",
                            statement=stmt.strip(),
                        )
                    )
    return salvaged


def extract_candidate_rules(
    batch: list[ThreadRecord],
    model: str = llm.MODEL_SYNTHESIS,
) -> list[CandidateRule]:
    """Pass 1 map: extract candidate rules from one batch of threads."""
    blobs = [b for b in (_thread_blob(r) for r in batch) if b]
    if not blobs:
        return []
    example = {
        "rules": [
            {
                "category": "metrics",
                "section": "experience",
                "applies_to": ["swe_intern"],
                "statement": "Quantify bullet impact with concrete metrics.",
                "supporting_thread_ids": ["123", "456"],
                "evidence_examples": ["add numbers to your bullets"],
            }
        ]
    }
    prompt = (
        "Extract consensus resume-critique RULES from these threads.\n"
        "Return ONLY JSON matching this exact shape (no other keys):\n"
        f"{json.dumps(example, indent=2)}\n\n"
        "Requirements:\n"
        f"- category ∈ {{{', '.join(RULE_CATEGORIES)}}}\n"
        f"- section ∈ {{{', '.join(RULE_SECTIONS)}}}\n"
        "- supporting_thread_ids MUST be thread_id values from the batch below\n"
        "- Extract 15–25 DISTINCT rules spanning many categories "
        "(metrics, wording, formatting, skills, projects, education, length, "
        "section_order, tailoring, redundancy_filler, bullet_quality, contact…)\n"
        "- statement = imperative community advice\n"
        "- evidence_examples = 1–3 short critique quotes\n"
        "- Include a rule even if only 1–2 threads support it "
        "(frequency filtering happens later)\n\n"
        "THREADS:\n\n"
        + "\n\n---\n\n".join(blobs)
    )
    raw = llm.complete(
        prompt=prompt,
        model=model,
        phase="phase3-rulebook-map",
        max_tokens=8192,
        system=MAP_SYSTEM,
    )
    rules = _parse_candidate_batch(raw)
    # Drop candidates with zero supporting IDs from the batch (hallucinated / unanchored)
    batch_ids = {r.thread_id for r in batch}
    anchored = []
    for c in rules:
        valid = [t for t in c.supporting_thread_ids if t in batch_ids]
        if not valid:
            # Keep statement but leave IDs empty — reduce/evidence may still merge
            # if another batch anchors it. Prefer keeping for reduce to see.
            anchored.append(c)
        else:
            anchored.append(c.model_copy(update={"supporting_thread_ids": valid}))
    return anchored


def merge_candidate_rules(
    candidates: list[CandidateRule],
    model: str = llm.MODEL_SYNTHESIS,
) -> list[Rule]:
    """Pass 2 reduce: merge candidates into canonical Rules (frequency = |thread_ids|)."""
    if not candidates:
        return []
    payload = []
    for c in candidates:
        payload.append(
            {
                "category": c.category,
                "section": c.section,
                "applies_to": c.applies_to,
                "statement": c.statement,
                "supporting_thread_ids": c.supporting_thread_ids,
                "evidence_examples": c.evidence_examples[:3],
            }
        )
    example = {
        "rules": [
            {
                "category": "metrics",
                "section": "experience",
                "applies_to": ["swe_intern"],
                "statement": "Quantify bullet impact with concrete metrics.",
                "supporting_thread_ids": ["123", "456", "789"],
                "evidence_examples": ["add numbers", "show % impact"],
            }
        ]
    }
    prompt = (
        "Merge these candidate rules into a canonical rulebook.\n"
        "Return ONLY JSON matching this exact shape:\n"
        f"{json.dumps(example, indent=2)}\n\n"
        "Union supporting_thread_ids for near-duplicates; keep 2–3 best "
        "evidence_examples; drop vague/contradictory rules.\n\n"
        "CANDIDATES:\n"
        + json.dumps({"rules": payload}, indent=2)
    )
    raw = llm.complete(
        prompt=prompt,
        model=model,
        phase="phase3-rulebook-reduce",
        max_tokens=8192,
        system=REDUCE_SYSTEM,
    )
    merged = _parse_candidate_batch(raw)

    rules: list[Rule] = []
    for c in merged:
        ids = sorted(set(c.supporting_thread_ids))
        if not c.statement.strip():
            continue
        rules.append(
            Rule(
                category=_normalize_category(c.category),
                section=_normalize_section(c.section),
                applies_to=c.applies_to or [],
                statement=c.statement.strip(),
                frequency=float(len(ids)),
                evidence_examples=(c.evidence_examples or [])[:3],
                supporting_thread_ids=ids,
            )
        )
    return rules


def _normalize_category(cat: str) -> str:
    c = (cat or "other").strip().lower().replace(" ", "_")
    return c if c in RULE_CATEGORIES else "other"


def _normalize_section(sec: str) -> str:
    s = (sec or "general").strip().lower()
    return s if s in RULE_SECTIONS else "general"


_STOP = frozenset(
    "a an the and or but if to of in on for with without from by is are was were be "
    "this that these those it its your you they them their resume resumes should "
    "must can will just also more less very not use using make sure keep".split()
)


def _keywords(text: str) -> set[str]:
    tokens = re.findall(r"[a-z0-9%]{4,}", (text or "").lower())
    return {t for t in tokens if t not in _STOP}


def anchor_rules_to_corpus(
    rules: list[Rule],
    records: list[ThreadRecord],
    min_keyword_hits: int = 3,
) -> list[Rule]:
    """
    Deterministically expand supporting_thread_ids by matching rule keywords /
    evidence quotes against critique text. Fixes LLM-omitted IDs so frequency
    reflects real corpus support.

    Requires ≥3 keyword hits (stricter than naive 2) to avoid over-counting
    common words like "bullet" / "resume".
    """
    thread_blobs: dict[str, str] = {}
    for rec in records:
        parts = [c.content or "" for c in rec.critiques]
        thread_blobs[rec.thread_id] = " \n ".join(parts).lower()

    anchored: list[Rule] = []
    for rule in rules:
        keys = _keywords(rule.statement)
        for ex in rule.evidence_examples:
            keys |= _keywords(ex)
        # Drop ultra-common tokens that match almost every thread
        keys -= {"bullet", "bullets", "point", "points", "section", "sections", "skill", "skills"}
        found = set(rule.supporting_thread_ids)
        for tid, blob in thread_blobs.items():
            if any((ex or "").lower() in blob for ex in rule.evidence_examples if len(ex) >= 12):
                found.add(tid)
                continue
            if not keys:
                continue
            hits = sum(1 for k in keys if k in blob)
            if hits >= min_keyword_hits:
                found.add(tid)
        ids = sorted(found)
        anchored.append(
            rule.model_copy(
                update={
                    "supporting_thread_ids": ids,
                    "frequency": float(len(ids)),
                }
            )
        )
    return anchored


def verify_rule_evidence(
    rules: list[Rule],
    corpus_thread_ids: set[str],
    min_frequency: int = DEFAULT_MIN_FREQUENCY,
) -> tuple[list[Rule], list[dict[str, Any]]]:
    """
    Hallucination guard: drop claimed thread_ids not in the corpus, recount
    frequency, discard rules below min_frequency or with empty statement.
    """
    kept: list[Rule] = []
    discarded: list[dict[str, Any]] = []
    for rule in rules:
        valid_ids = [tid for tid in rule.supporting_thread_ids if tid in corpus_thread_ids]
        invalid = [tid for tid in rule.supporting_thread_ids if tid not in corpus_thread_ids]
        freq = len(set(valid_ids))
        if not rule.statement.strip():
            discarded.append({"reason": "empty_statement", "statement": rule.statement})
            continue
        if freq < min_frequency:
            discarded.append(
                {
                    "reason": "below_min_frequency",
                    "statement": rule.statement,
                    "frequency": freq,
                    "invalid_ids": invalid,
                }
            )
            continue
        kept.append(
            rule.model_copy(
                update={
                    "supporting_thread_ids": sorted(set(valid_ids)),
                    "frequency": float(freq),
                    "evidence_examples": rule.evidence_examples[:3],
                }
            )
        )
        if invalid:
            discarded.append(
                {
                    "reason": "partial_invalid_ids_trimmed",
                    "statement": rule.statement,
                    "invalid_ids": invalid,
                    "kept_frequency": freq,
                }
            )
    # Sort by frequency desc
    kept.sort(key=lambda r: (-r.frequency, r.statement))
    return kept, discarded


def build_rulebook(
    records: list[ThreadRecord],
    batch_size: int = DEFAULT_BATCH_SIZE,
    min_frequency: int = DEFAULT_MIN_FREQUENCY,
    model: str = llm.MODEL_SYNTHESIS,
    yes: bool = False,
) -> dict[str, Any]:
    """Full map → reduce → evidence-check pipeline."""
    usable = [r for r in records if r.critiques]
    batches = batch_threads(usable, batch_size)
    n_calls = len(batches) + 1  # map batches + 1 reduce
    print(
        f"Rulebook: {len(usable)} threads with critiques → {len(batches)} map "
        f"batches (size={batch_size}) + 1 reduce on {model} (~{n_calls} API calls)."
    )
    if n_calls > 100 and not yes:
        raise SystemExit("Refusing >100 API calls without --yes (G3).")

    candidates: list[CandidateRule] = []
    for i, batch in enumerate(batches, 1):
        print(f"  map batch {i}/{len(batches)} ({len(batch)} threads)…")
        got = extract_candidate_rules(batch, model=model)
        print(f"    → {len(got)} candidates")
        candidates.extend(got)

    print(f"  reduce: merging {len(candidates)} candidates…")
    merged = merge_candidate_rules(candidates, model=model)
    print(f"    → {len(merged)} merged rules before anchoring")

    merged = anchor_rules_to_corpus(merged, records)
    print(
        f"    → after corpus anchor: "
        f"median freq={sorted(r.frequency for r in merged)[len(merged)//2] if merged else 0}"
    )

    corpus_ids = {r.thread_id for r in records}
    kept, discarded = verify_rule_evidence(merged, corpus_ids, min_frequency=min_frequency)
    print(
        f"  evidence check: kept {len(kept)}, discarded/trimmed notes {len(discarded)} "
        f"(min_frequency={min_frequency})"
    )

    return {
        "meta": {
            "n_threads": len(records),
            "n_threads_with_critiques": len(usable),
            "batch_size": batch_size,
            "min_frequency": min_frequency,
            "model": model,
            "n_candidates": len(candidates),
            "n_merged": len(merged),
            "n_kept": len(kept),
        },
        "rules": [r.model_dump() for r in kept],
        "discarded": discarded,
    }


def write_rulebook(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Phase 3 consensus rulebook.")
    parser.add_argument("--threads", type=Path, default=Path("data/structured/threads.jsonl"))
    parser.add_argument("--out", type=Path, default=Path("data/knowledge/rulebook.json"))
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument(
        "--min-frequency",
        type=int,
        default=None,
        help=f"Min supporting threads (default: {DEFAULT_MIN_FREQUENCY}, "
        f"or {PILOT_MIN_FREQUENCY} when corpus < 200).",
    )
    parser.add_argument("--model", default=llm.MODEL_SYNTHESIS)
    parser.add_argument("--yes", action="store_true", help="Confirm >100 API calls (G3).")
    args = parser.parse_args()

    if not args.threads.exists():
        raise SystemExit(f"{args.threads} not found.")

    records = load_threads(args.threads)
    min_freq = args.min_frequency
    if min_freq is None:
        min_freq = (
            PILOT_MIN_FREQUENCY if len(records) < 200 else DEFAULT_MIN_FREQUENCY
        )
        if min_freq != DEFAULT_MIN_FREQUENCY:
            print(
                f"Pilot corpus (n={len(records)}): using min_frequency={min_freq} "
                f"(PRD default is {DEFAULT_MIN_FREQUENCY}; override with --min-frequency)."
            )

    payload = build_rulebook(
        records,
        batch_size=args.batch_size,
        min_frequency=min_freq,
        model=args.model,
        yes=args.yes,
    )
    write_rulebook(payload, args.out)
    print(f"Wrote {args.out} with {payload['meta']['n_kept']} rules")
    for r in payload["rules"][:8]:
        print(f"  [{r['frequency']:.0f}] {r['section']}/{r['category']}: {r['statement'][:80]}")


if __name__ == "__main__":
    main()
