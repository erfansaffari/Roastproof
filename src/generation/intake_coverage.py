"""
Detect metrics/facts already present in intake so elicitation does not re-ask.

Coverage is precision-first: a question is covered only when the intake already
contains a metric in the SAME dimension the question asks about (users, leads,
percent, time, money, installs). Merely having *any* digit in the entry is not
enough — that over-filtering silenced elicitation on metric-rich intakes.
"""

from __future__ import annotations

import re
from typing import Iterable

from src.schemas import ElicitationQuestion, Intake, QAEntry, QAStore

_METRIC_SENT = re.compile(
    r"[^.!?\n]*\d[^.!?\n]*(?:[.!?]\s*|\n+|$)",
)
_USER_METRIC = re.compile(
    r"\d[\d,]*\+?\s*(?:active\s+)?(?:users?|students?|schools?|clients?|customers?)",
    re.I,
)
_LEAD_METRIC = re.compile(
    r"\d[\d,]*\+?\s*(?:qualified\s+)?leads?",
    re.I,
)
_PCT_METRIC = re.compile(r"\d+\s*%|\d+\s*percent", re.I)
_TIME_METRIC = re.compile(
    r"\d[\d,]*\+?\s*(?:ms|milliseconds?|seconds?|minutes?|hours?|days?|weeks?|"
    r"months?|years?|hrs?|mins?)\b|"
    r"(?:latency|p99|p95|response\s+time)[^.!?\n]*\d",
    re.I,
)
_MONEY_METRIC = re.compile(
    r"\$\s*\d[\d,]*(?:\.\d+)?[kKmMbB]?|"
    r"\d[\d,]*\+?\s*(?:USD|dollars?|revenue|ARR|MRR)\b",
    re.I,
)
_INSTALL_METRIC = re.compile(
    r"\d[\d,]*\+?\s*(?:installs?|downloads?|stars?|stars on GitHub|npm\s+downloads?)",
    re.I,
)
_ANY_METRIC = re.compile(r"\d")

# Topics that may be filtered/autofilled via intake metric coverage.
# vague_scope / expand_content / other / missing_skill are NEVER covered this way.
_COVERABLE_TOPICS = frozenset({"missing_metric"})


def _entry_texts(intake: Intake) -> list[tuple[str, str]]:
    """(label, text) for each experience/project."""
    out: list[tuple[str, str]] = []
    for exp in intake.experience or []:
        out.append((exp.company, exp.description or ""))
    for proj in intake.projects or []:
        out.append((proj.name, proj.description or ""))
    for edu in intake.education or []:
        out.append((edu.school, edu.details or ""))
    return out


def format_intake_metrics_block(intake: Intake) -> str:
    """Human-readable block of numeric facts already in the intake."""
    lines: list[str] = [
        "Metrics/facts ALREADY in the intake (these *dimensions* are COVERED — "
        "do NOT re-ask the same metric dimension; you MAY still ask ownership, "
        "scope, tradeoffs, or other missing_metric dimensions):"
    ]
    found = 0
    for label, text in _entry_texts(intake):
        for m in _METRIC_SENT.finditer(text):
            sent = m.group(0).strip()
            if not sent or not _ANY_METRIC.search(sent):
                continue
            lines.append(f"- [{label}] {sent}")
            found += 1
    if not found:
        lines.append("- (none detected)")
    return "\n".join(lines)


def _related_text(intake: Intake, relates_to: str) -> str:
    needle = (relates_to or "").lower()
    if not needle:
        return ""
    chunks: list[str] = []
    for label, text in _entry_texts(intake):
        lab = label.lower()
        if lab in needle or needle in lab or any(
            tok and tok in needle for tok in lab.replace("--", " ").split() if len(tok) > 3
        ):
            chunks.append(text)
    return "\n".join(chunks)


def _quote_for_match(text: str, match: re.Match[str]) -> str:
    """Prefer the surrounding metric sentence; else the match itself."""
    for sent in _METRIC_SENT.finditer(text):
        if match.group(0) in sent.group(0):
            return sent.group(0).strip()
    return match.group(0)


def covering_quote(intake: Intake, question: str, relates_to: str) -> str | None:
    """
    If intake already answers this missing_metric question *in the same
    dimension*, return a short quote. Else None.

    No generic fallback: having unrelated numbers in the entry does NOT cover
    a different metric dimension.
    """
    text = _related_text(intake, relates_to)
    if not text or not _ANY_METRIC.search(text):
        return None
    q = (question or "").lower()

    checks: list[tuple[re.Pattern[str], re.Pattern[str]]] = [
        (re.compile(r"user|participant|member|student|school|customer|client", re.I), _USER_METRIC),
        (re.compile(r"lead|pipeline|outreach|conversion", re.I), _LEAD_METRIC),
        (re.compile(r"percent|%|increase|reduc|cut|improv|growth", re.I), _PCT_METRIC),
        (
            re.compile(
                r"latency|response\s*time|p99|p95|how\s+long|duration|"
                r"milliseconds?|seconds?|minutes?|hours?|days?",
                re.I,
            ),
            _TIME_METRIC,
        ),
        (
            re.compile(r"\$|dollar|revenue|ARR|MRR|cost|budget|money|paid", re.I),
            _MONEY_METRIC,
        ),
        (
            re.compile(r"install|download|star|npm|homebrew|crate", re.I),
            _INSTALL_METRIC,
        ),
    ]
    for q_pat, m_pat in checks:
        if q_pat.search(q):
            m = m_pat.search(text)
            if m:
                return _quote_for_match(text, m)
            # Dimension asked but not present → not covered
            return None

    # No recognizable metric dimension in the question → not covered by metrics
    return None


def question_covered_by_intake(intake: Intake, q: ElicitationQuestion | QAEntry) -> bool:
    topic = (getattr(q, "topic", "") or "").lower()
    # Only missing_metric questions can be filtered via intake metric coverage.
    # Empty topic on a fresh LLM question: treat as missing_metric for safety
    # only when the question text clearly asks for a metric dimension.
    if topic and topic not in _COVERABLE_TOPICS:
        return False
    if not topic:
        # QAEntry / LLM without topic: only cover if question looks metric-like
        qtext = (q.question or "").lower()
        metric_like = bool(
            re.search(
                r"how\s+many|what\s+(?:was|is)\s+the\s+(?:number|percent|%|total)|"
                r"metric|users?|leads?|installs?|downloads?|latency|revenue|\$",
                qtext,
            )
        )
        if not metric_like:
            return False
    return covering_quote(intake, q.question, q.relates_to or "") is not None


def filter_questions_covered_by_intake(
    questions: Iterable[ElicitationQuestion],
    intake: Intake,
) -> list[ElicitationQuestion]:
    """Drop LLM questions whose answers are already in the intake text."""
    return [q for q in questions if not question_covered_by_intake(intake, q)]


def autofill_covered_pending(store: QAStore, intake: Intake) -> QAStore:
    """
    Mark pending sidecar questions as answered when intake already has the fact
    in the same metric dimension. Uses a verbatim quote from the intake.
    """
    changed = False
    updated: list[QAEntry] = []
    for q in store.questions:
        if q.status == "pending":
            # Force missing_metric-only path via question_covered_by_intake
            if question_covered_by_intake(intake, q):
                quote = covering_quote(intake, q.question, q.relates_to or "")
                if quote:
                    updated.append(
                        q.model_copy(
                            update={
                                "answer": quote,
                                "status": "answered",
                            }
                        )
                    )
                    changed = True
                    continue
        updated.append(q)
    if not changed:
        return store
    return store.model_copy(update={"questions": updated})
