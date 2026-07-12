"""
Phase 4.6/4.7 — corpus-grounded project portfolio evaluator.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from src.generation.generator import applicable_rules, format_rules_block, load_rulebook
from src.generation.prompts import (
    project_eval_system,
    project_eval_user,
    resolve_role_profile,
)
from src.knowledge.retrieve import retrieve
from src.llm import MODEL_SYNTHESIS, complete_json
from src.schemas import (
    FieldGap,
    Intake,
    ProjectEvalResult,
    ProjectVerdict,
    Suggestion,
)

DEFAULT_RULEBOOK = Path("data/knowledge/rulebook.json")


def _project_rules_block(intake: Intake, rulebook_path: Path = DEFAULT_RULEBOOK) -> str:
    rulebook = load_rulebook(rulebook_path)
    rules = applicable_rules(rulebook, intake, cap=20)
    project_rules = [
        r
        for r in rules
        if (r.get("section") or "").lower() in {"projects", "general"}
        or (r.get("category") or "").lower() in {"projects", "project_selection"}
    ]
    if not project_rules:
        project_rules = rules[:10]
    return format_rules_block(project_rules)


def _project_critiques_block(intake: Intake, k: int = 8) -> tuple[str, set[str]]:
    profile = intake.to_applicant_profile()
    query_parts = [
        intake.target_role,
        intake.profile_summary,
    ]
    for p in intake.projects:
        query_parts.append(f"{p.name} {p.technologies} {p.description[:200]}")
    query = " ".join(query_parts)
    points = retrieve(profile, "projects", query, k=k)
    ids = {p.id for p in points if p.id}
    general = retrieve(profile, "general", query + " project selection", k=4)
    for p in general:
        if p.id and p.id not in ids:
            points.append(p)
            ids.add(p.id)

    # Include critique ids so the model can ground evidence_ids.
    lines: list[str] = []
    for i, p in enumerate(points, 1):
        score = f"{p.score:.3f}" if p.score is not None else "?"
        lines.append(
            f"{i}. id={p.id} [{p.section}/{p.category}] (score={score}) {p.issue}"
        )
    block = "\n".join(lines) if lines else "(no critiques retrieved)"
    return block, ids


def _normalize_quote(text: str) -> str:
    t = (text or "").lower()
    t = re.sub(r"\s+", " ", t).strip()
    return t


def quote_in_block(quote: str, critiques_block: str, *, min_len: int = 12) -> bool:
    """True if evidence_quote appears (normalized) in the retrieval block."""
    q = _normalize_quote(quote)
    if len(q) < min_len:
        return False
    block = _normalize_quote(critiques_block)
    return q in block


def drop_ungrounded(
    result: ProjectEvalResult,
    allowed_ids: set[str],
    *,
    critiques_block: str = "",
) -> ProjectEvalResult:
    """Keep verdicts/gaps that cite retrieved critique ids or rule:; require quotes for gaps."""

    def _grounded(evid: list[str]) -> bool:
        if not evid:
            return False
        if not allowed_ids:
            return True
        for e in evid:
            e = str(e)
            if e.startswith("rule:"):
                return True
            if e in allowed_ids:
                return True
            if any(e in aid or aid in e for aid in allowed_ids):
                return True
        return False

    kept_projects = [pv for pv in result.projects if _grounded(list(pv.evidence_ids or []))]
    kept_gaps: list[FieldGap] = []
    for g in result.field_gaps:
        if not _grounded(list(g.evidence_ids or [])):
            continue
        # Verbatim quote required when we have a critiques block to check against
        if critiques_block and critiques_block != "(no critiques retrieved)":
            if not quote_in_block(g.evidence_quote or "", critiques_block):
                continue
        kept_gaps.append(g)

    # If the model returned projects but all failed grounding, keep them with a
    # synthetic note only when allowed_ids was empty (retrieval failed).
    if result.projects and not kept_projects and not allowed_ids:
        kept_projects = list(result.projects)

    return ProjectEvalResult(
        portfolio_composition=list(result.portfolio_composition or []),
        projects=kept_projects,
        field_gaps=kept_gaps,
    )


def eval_changed(prev: ProjectEvalResult | None, curr: ProjectEvalResult) -> bool:
    if prev is None:
        return True
    prev_gaps = sorted(g.gap.lower() for g in prev.field_gaps)
    curr_gaps = sorted(g.gap.lower() for g in curr.field_gaps)
    prev_verdicts = sorted((p.name, p.verdict) for p in prev.projects)
    curr_verdicts = sorted((p.name, p.verdict) for p in curr.projects)
    return prev_gaps != curr_gaps or prev_verdicts != curr_verdicts


def evaluate_projects(
    intake: Intake,
    *,
    rulebook_path: Path = DEFAULT_RULEBOOK,
    phase: str = "phase4.7-project-eval",
    prior_eval: ProjectEvalResult | None = None,
    prior_eval_path: Path | None = None,
) -> ProjectEvalResult:
    if not intake.projects:
        return ProjectEvalResult()

    if prior_eval is None and prior_eval_path and Path(prior_eval_path).exists():
        try:
            prior_eval = ProjectEvalResult.model_validate_json(
                Path(prior_eval_path).read_text(encoding="utf-8")
            )
        except Exception:
            prior_eval = None

    role = resolve_role_profile(intake.target_role)
    critiques_block, allowed_ids = _project_critiques_block(intake)
    rules_block = _project_rules_block(intake, rulebook_path)
    projects_json = json.dumps(
        [p.model_dump() for p in intake.projects],
        indent=2,
    )
    prior_json = prior_eval.model_dump_json(indent=2) if prior_eval else None
    raw = complete_json(
        prompt=project_eval_user(
            intake_projects_json=projects_json,
            critiques_block=critiques_block,
            rules_block=rules_block,
            role=role,
            prior_eval_json=prior_json,
        ),
        model=MODEL_SYNTHESIS,
        phase=phase,
        schema=ProjectEvalResult,
        system=project_eval_system(intake),
        max_tokens=4096,
        temperature=0,
    )
    return drop_ungrounded(raw, allowed_ids, critiques_block=critiques_block)


def project_eval_to_suggestions(result: ProjectEvalResult) -> list[Suggestion]:
    out: list[Suggestion] = []
    if result.portfolio_composition:
        comp = ", ".join(
            f"{c.name}→{c.domain}" for c in result.portfolio_composition[:8]
        )
        out.append(
            Suggestion(
                type="project_evaluation",
                detail=f"Portfolio composition: {comp}",
            )
        )
    for pv in result.projects:
        evid = ", ".join(pv.evidence_ids[:4]) if pv.evidence_ids else "community rules/critiques"
        improvements = "; ".join(pv.improvements[:4]) if pv.improvements else ""
        detail = (
            f"[{pv.name}] verdict={pv.verdict}. {pv.rationale}"
            f"{(' Improvements: ' + improvements) if improvements else ''}"
            f" (evidence: {evid})"
        )
        out.append(Suggestion(type="project_evaluation", detail=detail.strip()))
    for g in result.field_gaps:
        evid = ", ".join(g.evidence_ids[:4]) if g.evidence_ids else "community critiques"
        quote_bit = (
            f' quote="{g.evidence_quote[:120]}{"…" if len(g.evidence_quote) > 120 else ""}"'
            if g.evidence_quote
            else ""
        )
        out.append(
            Suggestion(
                type="project_evaluation",
                detail=(
                    f"Portfolio field gap for this role: {g.gap} "
                    f"(evidence: {evid}{quote_bit}). Consider a project in this area "
                    f"only if you can build/own it honestly."
                ),
            )
        )
    return out
