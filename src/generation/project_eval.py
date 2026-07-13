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
    ResumeContent,
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


def _generated_projects_json(resume: ResumeContent) -> str:
    """Project shape fed to the evaluator: name, tech, and generated bullets."""
    projects = []
    for p in resume.projects:
        projects.append(
            {
                "name": p.get("name", ""),
                "technologies": p.get("technologies", ""),
                "bullets": list(p.get("bullets", []) or []),
            }
        )
    return json.dumps(projects, indent=2)


def _project_critiques_block(
    intake: Intake,
    resume: ResumeContent,
    k: int = 8,
) -> tuple[str, set[str]]:
    profile = intake.to_applicant_profile()
    query_parts = [
        intake.target_role,
        intake.profile_summary,
    ]
    for p in resume.projects:
        bullets = " ".join(p.get("bullets", []) or [])
        query_parts.append(
            f"{p.get('name','')} {p.get('technologies','')} {bullets[:300]}"
        )
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


def _has_critique_id(evid: list[str], allowed_ids: set[str]) -> bool:
    """True if evidence cites at least one real retrieved critique id (not rule:)."""
    for e in evid:
        e = str(e)
        if e.startswith("rule:"):
            continue
        if e in allowed_ids:
            return True
        if any(e in aid or aid in e for aid in allowed_ids):
            return True
    return False


def _grounded_verdict(
    evid: list[str],
    allowed_ids: set[str],
    *,
    require_critique_id: bool,
) -> bool:
    if not evid:
        return False
    if not allowed_ids:
        # Retrieval failed — accept any non-empty evidence (incl. rule:)
        return bool(evid)
    if require_critique_id:
        return _has_critique_id(evid, allowed_ids)
    # Soft path (field gaps): rule: or critique id OK
    for e in evid:
        e = str(e)
        if e.startswith("rule:"):
            return True
        if e in allowed_ids:
            return True
        if any(e in aid or aid in e for aid in allowed_ids):
            return True
    return False


def drop_ungrounded(
    result: ProjectEvalResult,
    allowed_ids: set[str],
    *,
    critiques_block: str = "",
    require_critique_ids: bool | None = None,
) -> ProjectEvalResult:
    """
    Keep verdicts/gaps that cite retrieved critique ids.

    When retrieval returned critique ids, project verdicts MUST cite at least one
    real id — rule:-only evidence is rejected (the previous escape hatch).
    """
    if require_critique_ids is None:
        require_critique_ids = bool(allowed_ids)

    kept_projects = [
        pv
        for pv in result.projects
        if _grounded_verdict(
            list(pv.evidence_ids or []),
            allowed_ids,
            require_critique_id=require_critique_ids,
        )
    ]
    kept_gaps: list[FieldGap] = []
    for g in result.field_gaps:
        if not _grounded_verdict(
            list(g.evidence_ids or []),
            allowed_ids,
            require_critique_id=False,
        ):
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


def missing_improvements(result: ProjectEvalResult) -> list[str]:
    """Project names that lack a non-empty improvements list."""
    return [
        pv.name
        for pv in result.projects
        if not any(str(x).strip() for x in (pv.improvements or []))
    ]


def grounding_failures(
    result: ProjectEvalResult,
    allowed_ids: set[str],
) -> list[str]:
    """Project names whose evidence is rule-only or empty despite available ids."""
    if not allowed_ids:
        return []
    bad: list[str] = []
    for pv in result.projects:
        if not _has_critique_id(list(pv.evidence_ids or []), allowed_ids):
            bad.append(pv.name)
    return bad


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
    resume: ResumeContent,
    *,
    rulebook_path: Path = DEFAULT_RULEBOOK,
    phase: str = "phase5-project-eval",
    prior_eval: ProjectEvalResult | None = None,
    prior_eval_path: Path | None = None,
) -> ProjectEvalResult:
    if not resume.projects:
        return ProjectEvalResult()

    if prior_eval is None and prior_eval_path and Path(prior_eval_path).exists():
        try:
            prior_eval = ProjectEvalResult.model_validate_json(
                Path(prior_eval_path).read_text(encoding="utf-8")
            )
        except Exception:
            prior_eval = None

    role = resolve_role_profile(intake.target_role)
    critiques_block, allowed_ids = _project_critiques_block(intake, resume)
    rules_block = _project_rules_block(intake, rulebook_path)
    projects_json = _generated_projects_json(resume)
    prior_json = prior_eval.model_dump_json(indent=2) if prior_eval else None

    def _call(prompt: str) -> ProjectEvalResult:
        return complete_json(
            prompt=prompt,
            model=MODEL_SYNTHESIS,
            phase=phase,
            schema=ProjectEvalResult,
            system=project_eval_system(intake),
            max_tokens=4096,
            temperature=0,
        )

    base_prompt = project_eval_user(
        projects_json=projects_json,
        critiques_block=critiques_block,
        rules_block=rules_block,
        role=role,
        prior_eval_json=prior_json,
    )
    raw = _call(base_prompt)

    # Soft post-checks + one retry when critique ids exist but model used rule: only
    # or left improvements empty.
    bad_ground = grounding_failures(raw, allowed_ids)
    bad_impr = missing_improvements(raw)
    if allowed_ids and (bad_ground or bad_impr):
        err_bits = []
        if bad_ground:
            err_bits.append(
                "These projects used rule:-only or empty evidence_ids — cite real "
                f"critique id= values from the block instead: {bad_ground}"
            )
        if bad_impr:
            err_bits.append(
                "These projects have empty improvements — every verdict including "
                f"strong_keep needs ≥1 improvement: {bad_impr}"
            )
        retry_prompt = (
            base_prompt
            + "\n\n## Validation errors from previous attempt (fix and resubmit)\n"
            + "\n".join(f"- {b}" for b in err_bits)
        )
        raw = _call(retry_prompt)

    grounded = drop_ungrounded(raw, allowed_ids, critiques_block=critiques_block)

    # If grounding wiped all projects despite a full raw response, keep raw projects
    # that at least have improvements (better than empty eval) only when no ids —
    # otherwise return grounded (may be empty) so caller sees the failure mode.
    return grounded


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
