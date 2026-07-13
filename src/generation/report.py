"""
Phase 5 — user-facing Markdown suggestions report.

Combines (never fabricates):
  - critic issues fixed per round + remaining issues (each grounded);
  - existing Phase 4 suggestions (skills / metrics / content / project eval),
    with skill prevalence traced to norms.json;
  - elicitation / status summary (converged? pending questions?);
  - honest limitations (G1 — gaps are suggestions, never silently invented).

Pure function over already-written artifacts + intake/norms; no LLM calls.
"""

from __future__ import annotations

import re

from src.generation.generator import (
    load_norms,
    skill_prevalence_for_intake,
)
from src.schemas import Intake, QAStore


def _prevalence_for_detail(detail: str, prevalence: dict[str, float]) -> str:
    """Attach a real prevalence figure from norms if the missing skill is known."""
    d = detail.lower()
    best: tuple[str, float] | None = None
    for skill, freq in prevalence.items():
        if not skill:
            continue
        # Word-boundary match so short skills (C, Go, R) don't match inside words.
        pattern = r"(?<![a-z0-9+#])" + re.escape(skill.lower()) + r"(?![a-z0-9+#])"
        if re.search(pattern, d) and (best is None or freq > best[1]):
            best = (skill, freq)
    if best is None:
        return ""
    return f" ({best[0]} appears in {best[1]:.0%} of comparable resumes — norms.json)"


def _bucket_suggestions(suggestions: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for s in suggestions:
        out.setdefault(s.get("type", "other"), []).append(s)
    return out


def build_report(
    intake: Intake,
    *,
    suggestions: list[dict],
    revision_log: list[dict],
    critic_remaining: list[dict],
    status: dict,
    qa_store: QAStore | None = None,
    norms: dict | None = None,
) -> str:
    """Render the full Markdown report string."""
    norms = norms if norms is not None else load_norms()
    prevalence, bucket, thin = skill_prevalence_for_intake(norms, intake)

    buckets = _bucket_suggestions(suggestions)

    lines: list[str] = []
    lines.append(f"# Resume Review Report — {intake.name}")
    lines.append("")
    lines.append(f"Target role: **{intake.target_role}**")
    lines.append("")
    lines.append(
        "This report has two stages: **Input review** (questions we asked to "
        "extract your facts, before the resume existed) and **Output review** "
        "(how the finished resume reads once generated)."
    )
    lines.append("")

    # =======================================================================
    # STAGE A — INPUT REVIEW (before the resume exists): elicitation only
    # =======================================================================
    lines.append("## Stage A — Input review (questions)")
    converged = status.get("converged")
    pending = status.get("pending_questions", 0)
    if converged and not pending:
        lines.append(
            "- **Converged** — no further questions would materially strengthen "
            "the resume given the facts you provided."
        )
    elif pending:
        lines.append(
            f"- **{pending} pending question(s)** in the Q&A sidecar. "
            "Answer them and re-run for a stronger / fuller resume."
        )
        if qa_store is not None:
            for q in qa_store.questions:
                if q.status == "pending":
                    lines.append(f"  - {q.question}")
    else:
        lines.append(f"- Round {status.get('round', 0)}, not yet converged.")
    lines.append("")

    # =======================================================================
    # STAGE B — OUTPUT REVIEW (the finished resume): critic, portfolio, skills
    # =======================================================================
    lines.append("## Stage B — Output review (the generated resume)")
    lines.append("")

    # --- B1. Critic (bullet quality) ---------------------------------------
    lines.append("### Bullet quality (critic pass)")
    rounds = status.get("critic_rounds", 0)
    found = status.get("critic_issues_found", 0)
    fixed = status.get("critic_issues_fixed", 0)
    remaining = status.get("critic_issues_remaining", len(critic_remaining))
    lines.append(
        f"- Ran **{rounds}** revision round(s): "
        f"**{found}** issue(s) found, **{fixed}** bullet rewrite(s) applied, "
        f"**{remaining}** issue(s) remaining."
    )
    if revision_log:
        lines.append("")
        lines.append("#### Fixed this run")
        for entry in revision_log:
            rnd = entry.get("round")
            for ch in entry.get("changed", []):
                lines.append(
                    f"- **[{ch.get('entry','')}]** (round {rnd}) — "
                    f"{ch.get('addressed_issue','')}"
                )
                lines.append(f"  - before: {ch.get('original','')}")
                lines.append(f"  - after: {ch.get('revised','')}")
    if critic_remaining:
        lines.append("")
        lines.append("#### Remaining issues (grounded, not auto-fixed)")
        for it in critic_remaining:
            gid = it.get("rule_id") or ""
            cid = it.get("critique_id") or ""
            ground = ", ".join(x for x in (gid, f"critique:{cid}" if cid else "") if x)
            lines.append(
                f"- **[{it.get('entry','')}]** ({it.get('severity','')}) "
                f"{it.get('issue','')} — _fix:_ {it.get('suggested_fix','')} "
                f"`[{ground}]`"
            )
    if not revision_log and not critic_remaining:
        lines.append("- No grounded issues raised — the resume reads clean.")
    lines.append("")

    # --- B2. Project portfolio ---------------------------------------------
    if buckets.get("project_evaluation"):
        lines.append("### Project portfolio (corpus-grounded)")
        for s in buckets["project_evaluation"]:
            lines.append(f"- {s.get('detail','')}")
        lines.append("")

    # --- B3. Skill gaps (G1 — surfaced, never added) -----------------------
    if buckets.get("missing_skill"):
        lines.append("### Skills the community expects (not added — G1)")
        for s in buckets["missing_skill"]:
            detail = s.get("detail", "")
            lines.append(f"- {detail}{_prevalence_for_detail(detail, prevalence)}")
        lines.append("")

    # --- B4. Any other suggestion types (forward-compatible) ---------------
    other = {
        k: v
        for k, v in buckets.items()
        if k not in {"missing_skill", "project_evaluation"}
    }
    for k, items in other.items():
        lines.append(f"### {k}")
        for s in items:
            lines.append(f"- {s.get('detail','')}")
        lines.append("")

    # --- Limitations --------------------------------------------------------
    lines.append("## Limitations (honest)")
    lines.append(
        "- **G1 — no fabrication.** Missing skills, metrics, and experiences are "
        "surfaced above as suggestions; they are never silently added to the resume."
    )
    if thin:
        lines.append(
            f"- Norms for your bucket were sparse; figures fall back to the `{bucket}` "
            "family and should be read as directional."
        )
    lines.append(
        "- All prevalence figures trace to `data/norms/norms.json`; critic issues "
        "trace to community critique ids or rulebook rule ids."
    )
    lines.append("")

    return "\n".join(lines)
