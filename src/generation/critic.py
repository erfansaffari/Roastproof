"""
Phase 5 — corpus-grounded critic pass over a generated resume.

The critic re-reads the *generated* bullets, retrieves community critiques against
each bullet, and flags concrete weaknesses (vague scope, missing metric, fluff,
rule violations). Every issue must cite a real critique id or a real rule id;
ungrounded issues are dropped (same discipline as project_eval).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from src.generation.generator import (
    applicable_rules,
    fit_bullet_length,
    load_rulebook,
)
from src.generation.fluff import find_fluff_hits
from src.generation.project_eval import _has_critique_id
from src.generation.prompts import (
    critic_revise_instruction,
    critic_system,
    critic_user,
    resolve_role_profile,
    revise_system,
)
from src.knowledge.retrieve import retrieve
from src.llm import MODEL_SYNTHESIS, complete_json
from src.schemas import (
    CriticIssue,
    CriticResult,
    Intake,
    ResumeContent,
    RevisedBullet,
    RevisionResult,
)

DEFAULT_RULEBOOK = Path("data/knowledge/rulebook.json")

# Bullet sections the critic inspects (skills/education are not free-text bullets).
BULLET_SECTIONS = ("experience", "projects")
PER_BULLET_K = 3

# Instruction/meta phrases that must never leak into a resume bullet.
_LEAK_MARKERS = (
    "specify ",
    "add a metric",
    "add metric",
    "add scale",
    "quantify",
    "clarify",
    "consider adding",
    "provide details",
    "include the",
    "e.g.",
    "todo",
)


def _has_instruction_leak(text: str) -> bool:
    t = (text or "").lower()
    # A trailing "; ..." clause is the common leak shape from the fix text.
    tail = t.rsplit(";", 1)[-1] if ";" in t else t
    return any(m in t or m in tail for m in _LEAK_MARKERS)


def _rules_block_with_ids(
    intake: Intake,
    rulebook_path: Path = DEFAULT_RULEBOOK,
) -> tuple[str, set[str]]:
    """Format applicable rules with stable `rule:<n>` ids for grounding."""
    rulebook = load_rulebook(rulebook_path)
    rules = applicable_rules(rulebook, intake, cap=20)
    if not rules:
        return "(no applicable rules)", set()
    lines: list[str] = []
    ids: set[str] = set()
    for i, r in enumerate(rules, 1):
        rid = f"rule:{i}"
        ids.add(rid)
        lines.append(
            f"{i}. [{rid}] [{r.get('category')}/{r.get('section')}] "
            f"(freq={r.get('frequency')}) {r.get('statement')}"
        )
    return "\n".join(lines), ids


_NUMBER_RE = re.compile(r"\d")
_VAGUE_MARKERS = (
    "various", "several", "some ", "stuff", "things", "helped", "worked on",
    "assisted", "multiple projects", "as needed", "different tasks",
)


def bullet_gap_hints(resume: ResumeContent) -> list[dict]:
    """
    Deterministic (no-LLM) weak-bullet detector over the FINAL resume.

    Flags bullets with no numeric metric and/or vague-scope language. These are
    passed to the critic as hints so metric/scope weaknesses (previously Phase 4
    `missing_metric`/`content_gap` suggestions) are caught once, grounded, by the
    critic — not duplicated across systems.
    """
    hints: list[dict] = []
    for section, entry, bullet in iter_resume_bullets(resume):
        low = bullet.lower()
        gaps: list[str] = []
        if not _NUMBER_RE.search(bullet):
            gaps.append("no_metric")
        if any(m in low for m in _VAGUE_MARKERS):
            gaps.append("vague_scope")
        if gaps:
            hints.append(
                {"section": section, "entry": entry, "bullet": bullet, "gaps": gaps}
            )
    return hints


def _format_gap_hints(hints: list[dict]) -> str:
    if not hints:
        return ""
    lines = []
    for h in hints:
        lines.append(
            f"- [{h.get('entry','')}] ({', '.join(h.get('gaps', []))}) "
            f"{h.get('bullet','')}"
        )
    return "\n".join(lines)


def iter_resume_bullets(resume: ResumeContent):
    """Yield (section, entry_name, bullet_text) for every experience/project bullet."""
    for e in resume.experience:
        name = e.get("company") or e.get("title") or ""
        for b in e.get("bullets", []):
            yield "experience", name, b
    for p in resume.projects:
        name = p.get("name") or ""
        for b in p.get("bullets", []):
            yield "projects", name, b


def _bullet_critiques_block(
    intake: Intake,
    resume: ResumeContent,
    k: int = PER_BULLET_K,
) -> tuple[str, set[str]]:
    """
    Query the vector store with each generated bullet, dedupe by critique id,
    and format a numbered block with `id=` labels for grounding.
    """
    profile = intake.to_applicant_profile()
    by_id: dict[str, Any] = {}
    for section, _entry, bullet in iter_resume_bullets(resume):
        rsection = section if section in ("experience", "projects") else "general"
        points = retrieve(profile, rsection, bullet, k=k)
        for p in points:
            if p.id and p.id not in by_id:
                by_id[p.id] = p

    points = sorted(by_id.values(), key=lambda p: -(p.score or 0))
    ids = set(by_id.keys())
    lines: list[str] = []
    for i, p in enumerate(points, 1):
        score = f"{p.score:.3f}" if p.score is not None else "?"
        lines.append(
            f"{i}. id={p.id} [{p.section}/{p.category}] (score={score}) {p.issue}"
        )
    block = "\n".join(lines) if lines else "(no critiques retrieved)"
    return block, ids


def _issue_grounded(
    issue: CriticIssue,
    allowed_critique_ids: set[str],
    allowed_rule_ids: set[str],
) -> bool:
    cid = (issue.critique_id or "").strip()
    rid = (issue.rule_id or "").strip()
    if rid and rid in allowed_rule_ids:
        return True
    if cid and _has_critique_id([cid], allowed_critique_ids):
        return True
    return False


def drop_ungrounded_issues(
    result: CriticResult,
    allowed_critique_ids: set[str],
    allowed_rule_ids: set[str],
) -> CriticResult:
    """Keep only issues that cite a real critique id or a real rule id."""
    kept = [
        it
        for it in result.issues
        if _issue_grounded(it, allowed_critique_ids, allowed_rule_ids)
    ]
    return CriticResult(issues=kept)


def ungrounded_high_med(
    result: CriticResult,
    allowed_critique_ids: set[str],
    allowed_rule_ids: set[str],
) -> list[str]:
    """High/med issue texts that failed grounding (used for the one retry)."""
    bad: list[str] = []
    for it in result.issues:
        if it.severity in ("high", "med") and not _issue_grounded(
            it, allowed_critique_ids, allowed_rule_ids
        ):
            bad.append(it.issue)
    return bad


def high_severity_issues(result: CriticResult) -> list[CriticIssue]:
    return [it for it in result.issues if it.severity == "high"]


def high_med_issues(result: CriticResult) -> list[CriticIssue]:
    return [it for it in result.issues if it.severity in ("high", "med")]


def run_critic(
    intake: Intake,
    resume: ResumeContent,
    *,
    gap_hints: list[dict] | None = None,
    rulebook_path: Path = DEFAULT_RULEBOOK,
    phase: str = "phase5-critic",
) -> CriticResult:
    """Run one critic pass and return only grounded issues."""
    role = resolve_role_profile(intake.target_role)
    rules_block, allowed_rule_ids = _rules_block_with_ids(intake, rulebook_path)
    critiques_block, allowed_critique_ids = _bullet_critiques_block(intake, resume)
    resume_json = resume.model_dump_json(indent=2)
    if gap_hints is None:
        gap_hints = bullet_gap_hints(resume)
    gap_hints_block = _format_gap_hints(gap_hints)

    def _call(prompt: str) -> CriticResult:
        return complete_json(
            prompt=prompt,
            model=MODEL_SYNTHESIS,
            phase=phase,
            schema=CriticResult,
            system=critic_system(intake),
            max_tokens=3072,
            temperature=0,
        )

    base_prompt = critic_user(
        resume_json=resume_json,
        critiques_block=critiques_block,
        rules_block=rules_block,
        role=role,
        gap_hints_block=gap_hints_block or None,
    )
    raw = _call(base_prompt)

    bad = ungrounded_high_med(raw, allowed_critique_ids, allowed_rule_ids)
    if bad:
        retry_prompt = (
            base_prompt
            + "\n\n## Validation errors from previous attempt (fix and resubmit)\n"
            + "These high/med issues had no real critique_id or rule_id — either "
            + "add a real id copied verbatim from the blocks above, or drop them:\n"
            + "\n".join(f"- {b}" for b in bad)
        )
        raw = _call(retry_prompt)

    return drop_ungrounded_issues(raw, allowed_critique_ids, allowed_rule_ids)


def revise_bullets(
    intake: Intake,
    resume: ResumeContent,
    issues: list[CriticIssue],
    *,
    phase: str = "phase5-revise",
) -> tuple[ResumeContent, list[RevisedBullet]]:
    """
    Targeted per-bullet rewrite: rewrite ONLY the bullets flagged by high/med
    issues and splice them back in place. All other bullets stay byte-identical.

    Returns (revised_resume, diffs). No structural change to the resume — same
    entries, same bullet counts — so one-page layout is preserved.
    """
    targets = [
        it for it in issues if it.severity in ("high", "med") and (it.bullet_text or "").strip()
    ]
    if not targets:
        return resume, []

    instruction = critic_revise_instruction(targets)
    result = complete_json(
        prompt=instruction,
        model=MODEL_SYNTHESIS,
        phase=phase,
        schema=RevisionResult,
        system=revise_system(intake),
        max_tokens=1536,
        temperature=0,
    )

    # Map original bullet -> revised text (validated + fluff-guarded).
    rewrite_map: dict[str, str] = {}
    for rev in result.revisions:
        orig = (rev.original or "").strip()
        new = (rev.revised or "").strip()
        if not orig or not new or new == orig:
            continue
        # Reject instruction/meta text leaking into the resume bullet.
        if _has_instruction_leak(new):
            continue
        fitted = fit_bullet_length(new)
        if not fitted:
            continue
        # Reject a rewrite that introduces new fluff the original lacked.
        if find_fluff_hits(fitted) and not find_fluff_hits(orig):
            continue
        rewrite_map[orig] = fitted

    if not rewrite_map:
        return resume, []

    # Match each target's exact bullet_text to a revision.
    issue_by_bullet: dict[str, CriticIssue] = {}
    for it in targets:
        issue_by_bullet.setdefault((it.bullet_text or "").strip(), it)

    diffs: list[RevisedBullet] = []
    data = resume.model_dump()

    def _apply(section_key: str, entries: list[dict]) -> None:
        for entry in entries:
            name = entry.get("company") or entry.get("name") or entry.get("title") or ""
            bullets = entry.get("bullets") or []
            for idx, b in enumerate(bullets):
                key = (b or "").strip()
                if key in rewrite_map:
                    new_text = rewrite_map[key]
                    bullets[idx] = new_text
                    it = issue_by_bullet.get(key)
                    diffs.append(
                        RevisedBullet(
                            section=section_key,
                            entry=name,
                            original=b,
                            revised=new_text,
                            addressed_issue=it.issue if it else "",
                        )
                    )
            entry["bullets"] = bullets

    _apply("experience", data.get("experience") or [])
    _apply("projects", data.get("projects") or [])

    if not diffs:
        return resume, []

    try:
        revised = ResumeContent.model_validate(data)
    except Exception:
        # A revised bullet slipped the length bounds despite fit — keep original.
        return resume, []
    return revised, diffs


def issue_grounding_label(issue: CriticIssue) -> str:
    """Human-readable grounding id for the report."""
    parts = []
    if issue.rule_id:
        parts.append(issue.rule_id)
    if issue.critique_id:
        parts.append(f"critique:{issue.critique_id}")
    return ", ".join(parts) if parts else "(ungrounded)"
