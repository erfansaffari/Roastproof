"""
Phase 3 — ChromaDB critique vector store.

Explodes ThreadRecords (+ optional Phase-2 labels) into CritiquePoints,
embeds with sentence-transformers/all-MiniLM-L6-v2, persists under data/chroma/.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

from src.knowledge.norms import has_internships, infer_year_label, load_threads
from src.schemas import CritiquePoint, ThreadRecord

COLLECTION_NAME = "critiques_v1"
DEFAULT_PERSIST_DIR = Path("data/chroma")
DEFAULT_LABELS_PATH = Path("notebooks/critique_labels.jsonl")
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
# Cap only the *embedded* composite length; full issue is always stored in metadata.
EMBED_COMPOSITE_MAX_CHARS = 2000


def build_composite(
    target_role: str,
    profile_summary: str,
    section: str,
    original_text: str | None,
    issue: str,
    suggestion: str = "",
    max_chars: int | None = EMBED_COMPOSITE_MAX_CHARS,
) -> str:
    """Composite string embedded for retrieval (PRD format)."""
    resume_bit = (original_text or "").strip() or "(no quote)"
    if len(resume_bit) > 400:
        resume_bit = resume_bit[:399] + "…"
    sugg = (suggestion or "").strip()
    critique_bit = f"{issue} → {sugg}" if sugg else issue
    text = (
        f"[{target_role}] [{profile_summary}] [{section}] "
        f"resume text: {resume_bit} || critique: {critique_bit}"
    )
    if max_chars is not None and len(text) > max_chars:
        return text[: max_chars - 1] + "…"
    return text


def load_labels(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    """
    Index labels by (thread_id, content) for section/category join.
    Falls back gracefully if the labels file is missing.
    """
    if not path.exists():
        return {}
    out: dict[tuple[str, str], dict[str, Any]] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            key = (str(row.get("thread_id", "")), (row.get("content") or "").strip())
            out[key] = row
    return out


def explode_thread(
    rec: ThreadRecord,
    labels: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> list[CritiquePoint]:
    """
    Explode one thread into CritiquePoints.

    Skips empty issues and Phase-2 `not_a_critique` labels when available.
    """
    labels = labels or {}
    role = rec.target_role.value if hasattr(rec.target_role, "value") else str(rec.target_role)
    year = infer_year_label(rec) or "unknown"
    intern = has_internships(rec)
    profile = (rec.applicant_profile or rec.context_message or "")[:160]
    points: list[CritiquePoint] = []

    for i, crit in enumerate(rec.critiques):
        issue = (crit.content or "").strip()
        if not issue:
            continue
        label = labels.get((rec.thread_id, issue))
        if label and label.get("category") == "not_a_critique":
            continue
        section = (label or {}).get("section_targeted") or "general"
        category = (label or {}).get("category") or "other"
        # Critiques are usually advice themselves; keep suggestion empty unless
        # we later add an explicit split. Composite still formats cleanly.
        suggestion = ""
        composite = build_composite(
            target_role=role,
            profile_summary=profile,
            section=section,
            original_text=crit.original_text,
            issue=issue,
            suggestion=suggestion,
        )
        points.append(
            CritiquePoint(
                id=f"{rec.thread_id}:{i}",
                thread_id=rec.thread_id,
                target_role=role,
                section=section,
                year=year,
                has_internships=intern,
                agreement_signal=int(crit.agreement_signal or 0),
                issue=issue,
                suggestion=suggestion,
                original_text=crit.original_text,
                category=category,
                composite=composite,
            )
        )
    return points


def explode_corpus(
    records: list[ThreadRecord],
    labels_path: Path = DEFAULT_LABELS_PATH,
) -> list[CritiquePoint]:
    labels = load_labels(labels_path)
    points: list[CritiquePoint] = []
    for rec in records:
        points.extend(explode_thread(rec, labels))
    return points


def _embedding_fn():
    """Lazy local MiniLM embedder (no API cost)."""
    from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

    return SentenceTransformerEmbeddingFunction(model_name=EMBEDDING_MODEL)


def get_collection(persist_dir: Path = DEFAULT_PERSIST_DIR, create: bool = True):
    import chromadb

    persist_dir.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(persist_dir))
    ef = _embedding_fn()
    if create:
        return client.get_or_create_collection(
            name=COLLECTION_NAME,
            embedding_function=ef,
            metadata={"hnsw:space": "cosine"},
        )
    return client.get_collection(name=COLLECTION_NAME, embedding_function=ef)


def wipe_store(persist_dir: Path = DEFAULT_PERSIST_DIR) -> None:
    if persist_dir.exists():
        shutil.rmtree(persist_dir)
    persist_dir.mkdir(parents=True, exist_ok=True)


def build_vectorstore(
    records: list[ThreadRecord],
    persist_dir: Path = DEFAULT_PERSIST_DIR,
    labels_path: Path = DEFAULT_LABELS_PATH,
    rebuild: bool = False,
    batch_size: int = 64,
) -> dict[str, Any]:
    """Idempotent build of critiques_v1. --rebuild wipes first."""
    if rebuild:
        wipe_store(persist_dir)

    points = explode_corpus(records, labels_path=labels_path)
    if not points:
        raise SystemExit("No critique points to index.")

    collection = get_collection(persist_dir, create=True)
    existing = collection.count()
    if existing > 0 and not rebuild:
        print(
            f"Collection {COLLECTION_NAME} already has {existing} points; "
            "pass --rebuild to wipe and rebuild."
        )
        return {"n_points": existing, "rebuilt": False, "persist_dir": str(persist_dir)}

    ids = [p.id for p in points]
    documents = [p.composite for p in points]
    metadatas = [
        {
            "thread_id": p.thread_id,
            "role": p.target_role,
            "section": p.section,
            "year": p.year or "unknown",
            # Chroma metadata: use int, not bool
            "has_internships": 1 if p.has_internships else 0,
            "agreement_signal": int(p.agreement_signal),
            "category": p.category,
            # Full critique text for the generator (do NOT truncate here).
            "issue": p.issue,
        }
        for p in points
    ]

    for start in range(0, len(points), batch_size):
        end = start + batch_size
        collection.add(
            ids=ids[start:end],
            documents=documents[start:end],
            metadatas=metadatas[start:end],
        )
        print(f"  indexed {min(end, len(points))}/{len(points)}")

    return {
        "n_points": len(points),
        "rebuilt": rebuild or existing == 0,
        "persist_dir": str(persist_dir),
        "collection": COLLECTION_NAME,
        "embedding_model": EMBEDDING_MODEL,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Phase 3 Chroma critique store.")
    parser.add_argument("--threads", type=Path, default=Path("data/structured/threads.jsonl"))
    parser.add_argument("--persist-dir", type=Path, default=DEFAULT_PERSIST_DIR)
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS_PATH)
    parser.add_argument("--rebuild", action="store_true", help="Wipe and rebuild the collection.")
    args = parser.parse_args()

    if not args.threads.exists():
        raise SystemExit(f"{args.threads} not found.")

    records = load_threads(args.threads)
    print(f"Building {COLLECTION_NAME} from {len(records)} threads…")
    stats = build_vectorstore(
        records,
        persist_dir=args.persist_dir,
        labels_path=args.labels,
        rebuild=args.rebuild,
    )
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
