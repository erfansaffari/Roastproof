"""
Persistent elicitation Q&A sidecar next to the intake YAML.

Path convention: examples/my_intake.yaml → examples/my_intake.qa.yaml

User edits `answer:` in place:
  - non-empty string → answered
  - "skip" / "n/a" / "none" / "unknown" (case-insensitive) → declined
  - null / empty → pending
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import yaml

from src.schemas import ElicitationQuestion, Intake, QAEntry, QAStore

DECLINE_TOKENS = frozenset({"skip", "n/a", "na", "none", "unknown", "no", "n"})
DEFAULT_MAX_ROUNDS = 3
SEMANTIC_DEDUP_THRESHOLD = 0.80


def sidecar_path(intake_path: Path) -> Path:
    intake_path = Path(intake_path)
    return intake_path.with_suffix("").with_name(intake_path.stem + ".qa.yaml")


def normalize_question_text(text: str) -> str:
    t = (text or "").lower().strip()
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def stable_question_id(question: str, relates_to: str = "") -> str:
    """Content-hash id so identity survives across runs (not q1/q2)."""
    key = normalize_question_text(question)
    if relates_to:
        key = f"{key}|{normalize_question_text(relates_to)[:80]}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]
    # Human-readable slug from first few content words
    words = [w for w in normalize_question_text(question).split() if len(w) > 2][:3]
    slug = "-".join(words) if words else "q"
    return f"{slug}-{digest}"


def _classify_answer(raw: str | None) -> tuple[str | None, str]:
    """Return (answer_or_none, status)."""
    if raw is None:
        return None, "pending"
    s = str(raw).strip()
    if not s or s.lower() == "null":
        return None, "pending"
    if s.lower() in DECLINE_TOKENS:
        return s, "declined"
    return s, "answered"


def refresh_statuses(store: QAStore) -> QAStore:
    """Recompute status from answer fields (after user edits the sidecar)."""
    refreshed: list[QAEntry] = []
    for q in store.questions:
        answer, status = _classify_answer(q.answer)
        refreshed.append(
            q.model_copy(update={"answer": answer, "status": status})
        )
    return store.model_copy(update={"questions": refreshed})


def load_qa_store(path: Path) -> QAStore:
    path = Path(path)
    if not path.exists():
        return QAStore()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    store = QAStore.model_validate(data)
    return refresh_statuses(store)


def save_qa_store(store: QAStore, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    store = refresh_statuses(store)
    payload = store.model_dump(mode="python")
    # Prefer null for unanswered so YAML is easy to edit
    for q in payload.get("questions", []):
        if q.get("status") == "pending":
            q["answer"] = None
    text = yaml.safe_dump(
        payload,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    )
    header = (
        "# Roastproof elicitation Q&A sidecar — edit `answer:` in place.\n"
        "# Leave null to skip for now; set to 'skip' to decline (never re-ask).\n"
        "# Re-run the pipeline after answering; do not copy ids into intake.yaml.\n"
    )
    path.write_text(header + text, encoding="utf-8")
    return path


def merge_legacy_answers(store: QAStore, intake: Intake) -> QAStore:
    """
    Backward compat: if intake.answers has ids matching sidecar entries,
    apply those answers once. Orphan legacy answers become synthetic answered
    entries so the generator still sees the facts.
    """
    if not intake.answers:
        return store
    by_id = {q.id: q for q in store.questions}
    updated = list(store.questions)
    for qid, ans in intake.answers.items():
        answer, status = _classify_answer(ans)
        if qid in by_id:
            idx = next(i for i, q in enumerate(updated) if q.id == qid)
            if updated[idx].status == "pending" and status != "pending":
                updated[idx] = updated[idx].model_copy(
                    update={"answer": answer, "status": status}
                )
        else:
            # Orphan legacy answer — keep the fact for the generator
            updated.append(
                QAEntry(
                    id=str(qid),
                    round=max(store.round, 1),
                    topic="other",
                    impact="high",
                    question=f"(legacy answer {qid} — question text unavailable)",
                    relates_to="",
                    answer=answer,
                    status=status,
                )
            )
    return refresh_statuses(store.model_copy(update={"questions": updated}))


def format_history_block(store: QAStore) -> str:
    """Full prior Q&A history for the elicitation / generator prompts."""
    if not store.questions:
        return "(none — first elicitation round)"
    lines: list[str] = []
    for q in store.questions:
        if q.status == "answered":
            ans = q.answer or ""
            qtext = q.question
            if "question text unavailable" in (qtext or "").lower():
                lines.append(
                    f"- [{q.id}] COVERED FACT (legacy answer — do not re-ask this topic):\n"
                    f"  A: {ans}"
                )
            else:
                lines.append(f"- [{q.id}] Q: {qtext}\n  A: {ans}")
        elif q.status == "declined":
            lines.append(
                f"- [{q.id}] Q: {q.question}\n"
                f"  A: (declined — never re-ask; do not invent a number)"
            )
        else:
            lines.append(
                f"- [{q.id}] Q: {q.question}\n"
                f"  A: (pending — still unanswered; do not rephrase)"
            )
    # Explicit covered-facts list so the model can't miss answer content
    answered = [q for q in store.questions if q.status == "answered" and q.answer]
    if answered:
        lines.append("")
        lines.append("## Facts already in hand (topics these cover — do NOT re-ask)")
        for q in answered:
            lines.append(f"- {q.answer}")
    return "\n".join(lines)


def format_answers_block_from_store(store: QAStore) -> str:
    """Generator-facing Q→A pairs (answered + declined instructions)."""
    answered = [q for q in store.questions if q.status == "answered"]
    declined = [q for q in store.questions if q.status == "declined"]
    if not answered and not declined:
        return "(no answers yet — do not invent the missing facts)"
    lines: list[str] = [
        "User-provided elicitation answers (treat answered values as facts):"
    ]
    for q in answered:
        lines.append(f'- Q: "{q.question}"')
        lines.append(f"  A: {q.answer}")
        if q.relates_to:
            lines.append(f"  (relates_to: {q.relates_to})")
    if declined:
        lines.append("")
        lines.append(
            "Declined questions — user has no number/detail; "
            "do NOT invent one and do not flag the same gap again:"
        )
        for q in declined:
            lines.append(f'- Q: "{q.question}" (relates_to: {q.relates_to or "n/a"})')
    return "\n".join(lines)


def declined_relates_to(store: QAStore) -> list[str]:
    """Needles used to suppress repeat missing_metric suggestions for declined topics."""
    needles: list[str] = []
    for q in store.questions:
        if q.status != "declined":
            continue
        rt = (q.relates_to or q.question or "").lower().strip()
        if not rt:
            continue
        needles.append(rt)
        # Leading entity (company/project) before " - " / em-dash
        entity = rt.split(" - ")[0].split("—")[0].split("--")[0].strip()
        if entity and entity not in needles:
            needles.append(entity)
    return needles



def counts(store: QAStore) -> dict[str, int]:
    return {
        "pending": sum(1 for q in store.questions if q.status == "pending"),
        "answered": sum(1 for q in store.questions if q.status == "answered"),
        "declined": sum(1 for q in store.questions if q.status == "declined"),
        "total": len(store.questions),
    }


def _embed_texts(texts: list[str]) -> list[list[float]]:
    """Local MiniLM embeddings (same model as Chroma store)."""
    if not texts:
        return []
    from sentence_transformers import SentenceTransformer

    from src.knowledge.vectorstore import EMBEDDING_MODEL

    model = SentenceTransformer(EMBEDDING_MODEL)
    vectors = model.encode(texts, normalize_embeddings=True)
    return [v.tolist() for v in vectors]


def _cosine(a: list[float], b: list[float]) -> float:
    # Vectors are L2-normalized → cosine = dot product
    return sum(x * y for x, y in zip(a, b))


def semantic_dedup_questions(
    new_questions: list[ElicitationQuestion],
    prior: list[QAEntry],
    *,
    threshold: float = SEMANTIC_DEDUP_THRESHOLD,
) -> list[ElicitationQuestion]:
    """
    Drop new questions that are near-duplicates of prior ones (rephrases).
    Uses local MiniLM; falls back to normalized-string equality if embed fails.
    """
    if not new_questions:
        return []
    if not prior:
        return list(new_questions)

    prior_texts = [q.question for q in prior]
    new_texts = [q.question for q in new_questions]

    try:
        all_vecs = _embed_texts(prior_texts + new_texts)
        prior_vecs = all_vecs[: len(prior_texts)]
        new_vecs = all_vecs[len(prior_texts) :]
        kept: list[ElicitationQuestion] = []
        for q, vec in zip(new_questions, new_vecs):
            if any(_cosine(vec, pv) >= threshold for pv in prior_vecs):
                continue
            kept.append(q)
        return kept
    except Exception:
        # Deterministic fallback: exact normalized match
        prior_norm = {normalize_question_text(q.question) for q in prior}
        return [
            q
            for q in new_questions
            if normalize_question_text(q.question) not in prior_norm
        ]


def filter_by_round_impact(
    questions: list[ElicitationQuestion],
    *,
    next_round: int,
) -> list[ElicitationQuestion]:
    """Rounds 2+ only admit high-impact questions."""
    if next_round <= 1:
        return list(questions)
    return [q for q in questions if (q.impact or "high").lower() == "high"]


def append_new_questions(
    store: QAStore,
    questions: list[ElicitationQuestion],
    *,
    round_num: int,
) -> QAStore:
    """Append newly admitted questions as pending; assign stable hash ids."""
    existing_ids = {q.id for q in store.questions}
    existing_norm = {normalize_question_text(q.question) for q in store.questions}
    updated = list(store.questions)
    for q in questions:
        qid = q.id.strip() if q.id else ""
        if not qid or qid in existing_ids or qid.startswith("q") and qid[1:].isdigit():
            qid = stable_question_id(q.question, q.relates_to)
        if qid in existing_ids:
            continue
        if normalize_question_text(q.question) in existing_norm:
            continue
        entry = QAEntry(
            id=qid,
            round=round_num,
            topic=q.topic or "other",
            impact=(q.impact or "high").lower(),
            question=q.question,
            relates_to=q.relates_to or "",
            answer=None,
            status="pending",
        )
        updated.append(entry)
        existing_ids.add(qid)
        existing_norm.add(normalize_question_text(q.question))
    return store.model_copy(update={"questions": updated, "round": round_num})


def should_stop_elicitation(
    store: QAStore,
    *,
    model_complete: bool,
    new_surviving: int,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
) -> tuple[bool, str]:
    """
    Structural stopping rule.
    Returns (stop, reason).
    """
    if store.converged:
        return True, "already converged"
    if model_complete and new_surviving == 0:
        return True, "model marked complete with no new questions"
    if new_surviving == 0 and store.round >= 1 and counts(store)["answered"] + counts(store)["declined"] > 0:
        return True, "no new material questions after prior answers"
    if store.round >= max_rounds:
        return True, f"reached max elicit rounds ({max_rounds})"
    return False, ""
