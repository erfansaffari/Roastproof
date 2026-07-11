"""
Phase 3 — critique retrieval API.

retrieve(profile, section, query_text, k) → list[CritiquePoint]

Re-rank: score = 0.7·similarity + 0.2·profile_match(year, internships)
         + 0.1·normalized(agreement_signal)

Pilot decisions:
- General-blend: always mix section-specific + top general critiques.
- Unknown-year soft match: year=unknown gets neutral credit, never a penalty.
- Exclude positive_feedback at query time (kept in store, useless for generation).
- Cap agreement at 3 before normalizing; dedupe final top-k by thread_id.
- role_fallback: thin roles (ML, data, …) also query nearest big bucket (SWE).
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from src.knowledge.vectorstore import DEFAULT_PERSIST_DIR, get_collection
from src.schemas import ApplicantProfile, CritiquePoint

DEFAULT_SECTION_K = 5
DEFAULT_GENERAL_K = 3

# Categories kept in the store but excluded from generation retrieval.
EXCLUDED_RETRIEVAL_CATEGORIES = frozenset({"positive_feedback"})

# When a role bucket is thin, also search these roles (in order).
ROLE_FALLBACK: dict[str, list[str]] = {
    "Machine Learning Engineer": ["Software Engineer"],
    "Data Scientist": ["Software Engineer"],
    "Data Engineer": ["Software Engineer"],
    "Frontend Engineer": ["Software Engineer"],
    "Backend Engineer": ["Software Engineer"],
    "Full Stack Engineer": ["Software Engineer"],
    "DevOps Engineer": ["Software Engineer"],
    "Site Reliability Engineer": ["Software Engineer"],
    "QA Engineer": ["Software Engineer"],
}

# Agreement above this no longer increases the re-rank bonus (viral-comment cap).
AGREEMENT_CAP = 3


def profile_match(
    query_year: str | None,
    query_has_internships: bool,
    doc_year: str | None,
    doc_has_internships: bool,
) -> float:
    """
    Profile similarity in [0, 1].

    Unknown-year soft match: if either side is unknown/missing, award 0.5 for
    the year component (neutral) instead of 0 (mismatch penalty).
    """
    qy = (query_year or "unknown").lower()
    dy = (doc_year or "unknown").lower()

    if qy == "unknown" or dy == "unknown":
        year_score = 0.5
    elif qy == dy:
        year_score = 1.0
    else:
        year_score = 0.0

    intern_score = 1.0 if bool(query_has_internships) == bool(doc_has_internships) else 0.0
    return 0.7 * year_score + 0.3 * intern_score


def normalize_agreement(signal: int, cap: int = AGREEMENT_CAP) -> float:
    """
    Map agreement_signal into [0, 1], clamping at `cap` so a single viral
    comment (agree=9) cannot dominate every general query.
    """
    if cap <= 0:
        return 0.0
    clamped = max(0, min(int(signal), cap))
    return float(clamped) / float(cap)


def normalize_agreement_log(signal: int, cap: int = AGREEMENT_CAP) -> float:
    """Optional log-scaled variant (same cap); kept for experiments."""
    if cap <= 0:
        return 0.0
    clamped = max(0, min(int(signal), cap))
    return math.log1p(clamped) / math.log1p(cap)


def rerank_score(
    similarity: float,
    profile_match_score: float,
    agreement_signal: int,
    w_sim: float = 0.7,
    w_profile: float = 0.2,
    w_agree: float = 0.1,
) -> float:
    """Weighted retrieval score (PRD formula + agreement cap)."""
    return (
        w_sim * similarity
        + w_profile * profile_match_score
        + w_agree * normalize_agreement(agreement_signal)
    )


def resolve_roles(target_role: str) -> list[str]:
    """Primary role plus fallbacks for thin buckets."""
    roles = [target_role]
    for fb in ROLE_FALLBACK.get(target_role, []):
        if fb not in roles:
            roles.append(fb)
    return roles


def _chroma_distance_to_similarity(distance: float | None) -> float:
    """Cosine distance → similarity in ~[0, 1]."""
    if distance is None:
        return 0.0
    return max(0.0, min(1.0, 1.0 - float(distance)))


def _rows_from_query(result: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    ids = (result.get("ids") or [[]])[0]
    docs = (result.get("documents") or [[]])[0]
    metas = (result.get("metadatas") or [[]])[0]
    dists = (result.get("distances") or [[]])[0]
    for i, pid in enumerate(ids):
        meta = metas[i] if i < len(metas) else {}
        dist = dists[i] if i < len(dists) else None
        # Query-time filter: positive_feedback stays in the store but is not
        # useful as generation guidance.
        if (meta or {}).get("category") in EXCLUDED_RETRIEVAL_CATEGORIES:
            continue
        rows.append(
            {
                "id": pid,
                "document": docs[i] if i < len(docs) else "",
                "metadata": meta or {},
                "similarity": _chroma_distance_to_similarity(dist),
            }
        )
    return rows


def _point_from_row(row: dict[str, Any], score: float) -> CritiquePoint:
    meta = row["metadata"]
    # Prefer full issue from metadata; fall back to parsing composite document.
    issue = str(meta.get("issue") or "")
    if not issue and row.get("document"):
        doc = row["document"]
        marker = "|| critique: "
        if marker in doc:
            issue = doc.split(marker, 1)[1]
            if " → " in issue:
                issue = issue.split(" → ", 1)[0]
    return CritiquePoint(
        id=row["id"],
        thread_id=str(meta.get("thread_id", "")),
        target_role=str(meta.get("role", "")),
        section=str(meta.get("section", "general")),
        year=str(meta.get("year", "unknown")),
        has_internships=bool(meta.get("has_internships", 0)),
        agreement_signal=int(meta.get("agreement_signal", 0) or 0),
        issue=issue,
        suggestion="",
        original_text=None,
        category=str(meta.get("category", "other")),
        composite=row.get("document") or "",
        score=score,
    )


def _query_section(
    collection,
    query_text: str,
    roles: list[str],
    section: str,
    n: int,
) -> list[dict[str, Any]]:
    """Query one section across primary + fallback roles; merge unique ids."""
    if n <= 0:
        return []
    # Over-fetch so filters (positive_feedback, role fallback merge) still leave enough.
    over = max(n * 4, n)
    by_id: dict[str, dict[str, Any]] = {}

    for role in roles:
        where: dict[str, Any] = {"$and": [{"role": role}, {"section": section}]}
        try:
            result = collection.query(
                query_texts=[query_text],
                n_results=over,
                where=where,
                include=["documents", "metadatas", "distances"],
            )
            for row in _rows_from_query(result):
                prev = by_id.get(row["id"])
                if prev is None or row["similarity"] > prev["similarity"]:
                    by_id[row["id"]] = row
        except Exception:
            continue

    if by_id:
        return sorted(by_id.values(), key=lambda r: -r["similarity"])

    # Last resort: section-only (no role filter).
    result = collection.query(
        query_texts=[query_text],
        n_results=over,
        where={"section": section},
        include=["documents", "metadatas", "distances"],
    )
    return _rows_from_query(result)


def _score_rows(
    rows: list[dict[str, Any]],
    profile: ApplicantProfile,
) -> list[tuple[float, dict[str, Any]]]:
    scored: list[tuple[float, dict[str, Any]]] = []
    for row in rows:
        meta = row["metadata"]
        pm = profile_match(
            profile.year,
            profile.has_internships,
            meta.get("year"),
            bool(meta.get("has_internships", 0)),
        )
        score = rerank_score(
            similarity=row["similarity"],
            profile_match_score=pm,
            agreement_signal=int(meta.get("agreement_signal", 0) or 0),
        )
        scored.append((score, row))
    scored.sort(key=lambda x: -x[0])
    return scored


def _take_deduped(
    scored: list[tuple[float, dict[str, Any]]],
    n: int,
    seen_ids: set[str],
    seen_threads: set[str],
) -> list[CritiquePoint]:
    """Take up to n points, unique by critique id and by thread_id."""
    out: list[CritiquePoint] = []
    for score, row in scored:
        if row["id"] in seen_ids:
            continue
        tid = str((row["metadata"] or {}).get("thread_id", ""))
        if tid and tid in seen_threads:
            continue
        seen_ids.add(row["id"])
        if tid:
            seen_threads.add(tid)
        out.append(_point_from_row(row, score))
        if len(out) >= n:
            break
    return out


def retrieve(
    profile: ApplicantProfile,
    section: str,
    query_text: str,
    k: int = 8,
    section_k: int = DEFAULT_SECTION_K,
    general_k: int = DEFAULT_GENERAL_K,
    persist_dir: Path = DEFAULT_PERSIST_DIR,
    collection=None,
) -> list[CritiquePoint]:
    """
    Retrieve top critiques for a profile + section.

    General-blend: take up to `section_k` from the requested section and
    `general_k` from `general`, then pad to `k` from the combined pool.
    Final top-k is deduped by thread_id so one viral thread cannot fill
    multiple slots.
    """
    if collection is None:
        collection = get_collection(persist_dir, create=False)

    section = (section or "general").lower()
    roles = resolve_roles(profile.target_role)

    if section == "general":
        sk, gk = k, 0
    else:
        sk = min(section_k, k)
        gk = min(general_k, max(0, k - sk))

    section_scored = _score_rows(
        _query_section(collection, query_text, roles, section, max(sk, 1)),
        profile,
    )
    general_scored = (
        _score_rows(
            _query_section(collection, query_text, roles, "general", max(gk, 1)),
            profile,
        )
        if gk > 0
        else []
    )

    seen_ids: set[str] = set()
    seen_threads: set[str] = set()
    out: list[CritiquePoint] = []

    out.extend(_take_deduped(section_scored, sk, seen_ids, seen_threads))
    out.extend(_take_deduped(general_scored, gk, seen_ids, seen_threads))

    if len(out) < k:
        pool = section_scored + general_scored
        pool.sort(key=lambda x: -x[0])
        out.extend(_take_deduped(pool, k - len(out), seen_ids, seen_threads))

    out.sort(key=lambda p: -(p.score or 0))
    return out[:k]


def format_for_prompt(points: list[CritiquePoint], max_chars: int | None = None) -> str:
    """
    Compact numbered block for generator prompts.

    Full critique text by default. Pass max_chars only for terminal display
    truncation — never for the generator prompt path.
    """
    if not points:
        return "(no critiques retrieved)"
    lines = []
    for i, p in enumerate(points, 1):
        score = f"{p.score:.3f}" if p.score is not None else "?"
        quote = f' (resume: "{p.original_text[:80]}")' if p.original_text else ""
        issue = p.issue
        if max_chars is not None and len(issue) > max_chars:
            issue = issue[: max_chars - 1] + "…"
        lines.append(
            f"{i}. [{p.section}/{p.category}] (score={score}, agree={p.agreement_signal}) "
            f"{issue}{quote}"
        )
    return "\n".join(lines)
