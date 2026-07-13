"""
Phase 4.6 — centralized prompt library.

Persona = judgment (big-tech hiring screen). Dataset = facts (rules, critiques,
norms, mined artifacts). Where they conflict, the data wins.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.schemas import (
    BULLET_MAX_LEN,
    BULLET_MIN_LEN,
    MAX_BULLETS_PER_EXPERIENCE,
    MAX_BULLETS_PER_PROJECT,
    MAX_PROJECTS,
    MAX_TOTAL_BULLETS,
    Intake,
)

DATA_WINS_CLAUSE = (
    "Where your general knowledge conflicts with the community data below, "
    "the data wins; where the data is silent, say so rather than asserting."
)

G1_CLAUSE = (
    "G1 — No fabrication. NEVER invent skills, tools, metrics, numbers, dates, "
    "titles, or experiences the user did not provide. Gaps → suggestions only."
)

REFUSAL_CLAUSE = (
    "If information is missing, FLAG it (gaps / suggestions / questions) — "
    "never invent to fill the hole."
)


@dataclass(frozen=True)
class RoleProfile:
    key: str
    display_name: str
    scan_first: str
    decision_sections: tuple[str, ...]
    norms_hint: str
    thin_bucket_note: str = ""


ROLE_PROFILES: dict[str, RoleProfile] = {
    "software_engineer": RoleProfile(
        key="software_engineer",
        display_name="Software Engineer",
        scan_first=(
            "impact bullets with scope + tech + outcome; shipped systems; "
            "internships/co-ops with concrete ownership"
        ),
        decision_sections=("experience", "projects", "skills"),
        norms_hint="swe / swe_intern / swe_new_grad",
    ),
    "ml_engineer": RoleProfile(
        key="ml_engineer",
        display_name="Machine Learning Engineer",
        scan_first=(
            "modeling projects with eval metrics, data scale, training/serving "
            "path; ablation or baseline comparisons when claimed"
        ),
        decision_sections=("projects", "experience", "skills"),
        norms_hint="ml (thin — may fall back to SWE critiques)",
        thin_bucket_note="ML critiques are sparse; SWE-family retrieval may be mixed in.",
    ),
    "ai_engineer": RoleProfile(
        key="ai_engineer",
        display_name="AI Engineer",
        scan_first=(
            "LLM/agent systems with measurable outcomes, evals, latency/cost, "
            "retrieval quality — not just 'used GPT'"
        ),
        decision_sections=("projects", "experience", "skills"),
        norms_hint="ml / swe (thin AI-specific bucket)",
        thin_bucket_note="AI-specific corpus is thin; ground claims in retrieved critiques.",
    ),
    "frontend": RoleProfile(
        key="frontend",
        display_name="Frontend Engineer",
        scan_first=(
            "shipped UIs, performance numbers, accessibility, design collaboration, "
            "framework depth shown in bullets not skill soup"
        ),
        decision_sections=("experience", "projects", "skills"),
        norms_hint="swe family (frontend folds into swe)",
    ),
    "backend": RoleProfile(
        key="backend",
        display_name="Backend Engineer",
        scan_first=(
            "scale, latency, reliability, APIs, data stores, ownership of "
            "production paths with numbers when available"
        ),
        decision_sections=("experience", "projects", "skills"),
        norms_hint="swe family",
    ),
    "fullstack": RoleProfile(
        key="fullstack",
        display_name="Full Stack Engineer",
        scan_first=(
            "end-to-end ownership across client + API + data; clear scope per bullet"
        ),
        decision_sections=("experience", "projects", "skills"),
        norms_hint="swe family",
    ),
    "devops": RoleProfile(
        key="devops",
        display_name="DevOps / SRE",
        scan_first=(
            "infra as code, CI/CD, observability, incident response, reliability metrics"
        ),
        decision_sections=("experience", "projects", "skills"),
        norms_hint="swe family (thin specialty)",
        thin_bucket_note="DevOps/SRE-specific critiques are thin in this corpus.",
    ),
    "data": RoleProfile(
        key="data",
        display_name="Data Scientist / Data Engineer",
        scan_first=(
            "data pipelines or analyses with scale, methods, and measurable outcomes"
        ),
        decision_sections=("projects", "experience", "skills"),
        norms_hint="data / data_intern (may be thin)",
        thin_bucket_note="Data buckets may be under min-n; check norms.insufficient_data.",
    ),
}


def resolve_role_profile(target_role: str) -> RoleProfile:
    """Map free-text target_role to a RoleProfile."""
    r = (target_role or "").lower()
    if "machine learning" in r or r.strip() in {"ml", "ml engineer", "mle"}:
        return ROLE_PROFILES["ml_engineer"]
    if "ai engineer" in r or "ai eng" in r or r.strip() == "ai":
        return ROLE_PROFILES["ai_engineer"]
    if "front" in r:
        return ROLE_PROFILES["frontend"]
    if "back" in r:
        return ROLE_PROFILES["backend"]
    if "full" in r and "stack" in r:
        return ROLE_PROFILES["fullstack"]
    if "devops" in r or "sre" in r or "reliability" in r:
        return ROLE_PROFILES["devops"]
    if "data" in r:
        return ROLE_PROFILES["data"]
    return ROLE_PROFILES["software_engineer"]


def hiring_persona(role: RoleProfile) -> str:
    note = f"\nNote: {role.thin_bucket_note}" if role.thin_bucket_note else ""
    return (
        f"You are a senior engineer at a top tech company screening resumes for "
        f"{role.display_name}. You see ~200 resumes per opening and decide in "
        f"~30 seconds what earns an interview.\n"
        f"In the first pass you scan for: {role.scan_first}.\n"
        f"Sections that usually carry the interview decision: "
        f"{', '.join(role.decision_sections)}.\n"
        f"Norms bucket hint (facts from norms.json, not your memory): {role.norms_hint}."
        f"{note}\n"
        f"{DATA_WINS_CLAUSE}"
    )


def assert_prompt_invariants(text: str, *, require_output_contract: bool = True) -> list[str]:
    """Return list of missing invariant names (empty = OK). Used in unit tests."""
    missing: list[str] = []
    lower = text.lower()
    if "200 resumes" not in lower and "hiring" not in lower and "screening" not in lower:
        # Analyst prompts (miners) use a different persona — allow analyst marker
        if "extract only what" not in lower and "verbatim" not in lower:
            missing.append("persona")
    if "data wins" not in lower and "community data" not in lower:
        if "extract only what" not in lower:  # miners cite critiques instead
            missing.append("data_wins")
    if require_output_contract and "json" not in lower:
        missing.append("output_contract")
    return missing


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------

def generator_system(
    intake: Intake,
    banned_phrases: list[str] | None = None,
    *,
    bullet_targets: str | None = None,
) -> str:
    role = resolve_role_profile(intake.target_role)
    banned = banned_phrases or []
    banned_line = (
        ", ".join(banned[:40])
        if banned
        else "seamless, robust, effective, enhanced, streamlined, ensure, utilized"
    )
    targets = bullet_targets or (
        "Target community upper-band density when intake material allows; "
        "do not leave thin entries if unused facts remain."
    )
    return f"""{hiring_persona(role)}

You are rewriting this applicant's resume for a 30-second screen. You are a WRITER, not a formatter.

## Two axes (do not collapse them)
1. FACTS ARE FROZEN — {G1_CLAUSE}
2. WORDING IS MANDATORY TO REWRITE: You MUST rewrite every bullet to comply with the
community rules and critiques in the user message. Copying intake prose verbatim is a failure.
Preserve all facts (numbers, tools, scope, outcomes) exactly as given; never add facts.
If a bullet lacks a metric, do NOT invent one — rewrite for clarity and set gaps to include "no_metric".

Ask of every bullet: would this line survive a 30-second screen for {role.display_name}?

## Banned empty wording (community-flagged; never use unless a number/tool immediately substantiates)
{banned_line}

## Output JSON contract
{{
  "resume": {{
    "contact": {{"name","email","phone","linkedin","github","website"}},
    "education": [{{"school","degree","dates","location","details"}}],
    "experience": [{{
      "company","title","dates","location","technologies",
      "bullets": [{{"text": str, "rewritten_from": str, "gaps": ["no_metric"|"vague_scope"]}}]
    }}],
    "projects": [{{
      "name","technologies","dates",
      "bullets": [{{"text": str, "rewritten_from": str, "gaps": [...]}}]
    }}],
    "skills": {{"Languages": [...], "Frameworks": [...], "Developer Tools": [...], "Libraries": [...]}},
    "section_order": ["education","experience","projects","skills"]
  }},
  "suggestions": [{{"type": "missing_skill|missing_metric|content_gap|project_evaluation", "detail": str}}]
}}

Bullet constraints (ceilings are safety caps — fill toward community targets):
- Each bullet text length {BULLET_MIN_LEN}–{BULLET_MAX_LEN} chars.
- ≤{MAX_BULLETS_PER_EXPERIENCE} bullets/experience, ≤{MAX_BULLETS_PER_PROJECT}/project,
  ≤{MAX_PROJECTS} projects, ≤{MAX_TOTAL_BULLETS} bullets total.
- Community density targets: {targets}
- Prefer the upper band (p75) when unused intake facts exist; thin 1–2 bullet entries are a failure
  when the intake description clearly supports more distinct accomplishments.
- Every bullet MUST set rewritten_from to the intake phrase it came from.
- gaps: "no_metric" when impact has no number; "vague_scope" when scope is unclear; [] when solid.
- technologies (experience AND projects): comma-separated stack line for that entry ONLY.
  Extract tool/framework names attested in that entry's intake description, intake.technologies
  field, or answered QA that relates to that entry. If none are attested, use "".
  NEVER invent tools from role norms or "typical stacks" — corpus prevalence is not a fact source.
  REQUIRED when the description names tools (e.g. React, Next.js, n8n, Convex): you MUST fill
  technologies — leaving it "" while tools appear in the description is a failure.
- Skills: ONLY skills the user listed (or named in experience/project technologies). Never pad for breadth.

{REFUSAL_CLAUSE}
"""


def generator_user_prompt(
    *,
    few_shots: str,
    rules_block: str,
    critiques_block: str,
    norms_block: str,
    answers_block: str,
    intake_block: str,
    role: RoleProfile,
    trim_instruction: str | None = None,
    fluff_instruction: str | None = None,
    expand_instruction: str | None = None,
    preferred_patterns: list[str] | None = None,
) -> str:
    patterns = preferred_patterns or []
    pattern_block = (
        "\n".join(f"- {p}" for p in patterns[:12])
        if patterns
        else "(see community rules and critiques)"
    )
    parts = [
        f"## Role screen focus ({role.display_name})",
        f"Scan first: {role.scan_first}",
        "",
        "## Few-shot rewrites (community-grounded — match this bar)",
        few_shots.strip() or "(no mined examples yet — follow rules/critiques)",
        "",
        "## Preferred patterns (MUST apply)",
        pattern_block,
        "",
        "## Applicable community rules (MUST apply while rewriting)",
        rules_block,
        "",
        "## Retrieved community critiques (MUST apply — avoid patterns they flag)",
        critiques_block,
        "",
        "## Norms / skill prevalence + bullet density (MUST apply)",
        norms_block,
        "",
        "## Elicitation answers (treat as facts)",
        answers_block,
        "",
        "## Applicant intake (source of facts — REWRITE wording; do not invent beyond this)",
        intake_block,
    ]
    if trim_instruction:
        parts.extend(["", "## Page-fit trim instruction", trim_instruction])
    if expand_instruction:
        parts.extend(["", "## Page-fill expand instruction", expand_instruction])
    if fluff_instruction:
        parts.extend(["", "## Fluff lint retry", fluff_instruction])
    parts.append(
        "\nReturn the JSON object now. Rewrite every bullet. Facts frozen. "
        "Attest rewritten_from + gaps on each bullet."
    )
    return "\n".join(parts)


def pagefit_trim_instruction(
    *,
    pages: int,
    cut_lines: int,
    bullet_count: int,
    role: RoleProfile,
) -> str:
    return (
        f"Hiring screen for {role.display_name}: the previous draft compiled to "
        f"{pages} pages. You have ~30 seconds of reader attention — cut the "
        f"lowest-value bullets and tighten wording to target roughly {cut_lines} "
        f"fewer lines. Prefer dropping weak project bullets first. Keep G1 — do "
        f"not invent content to fill space. Current bullet count ≈ {bullet_count}."
    )


def pagefit_expand_instruction(
    *,
    fill_ratio: float,
    fill_target: float,
    bullet_count: int,
    thin_entries: list[str],
    unused_facts: list[str],
    role: RoleProfile,
    bullets_per_entry_p75: float | None = None,
) -> str:
    thin = "\n".join(f"- {t}" for t in thin_entries) or "(none flagged)"
    unused = "\n".join(f"- {f}" for f in unused_facts[:20]) or (
        "(no unused intake sentences detected — do not invent; keep existing facts)"
    )
    band = (
        f"Community upper band ≈ {bullets_per_entry_p75:.1f} bullets/entry. "
        if bullets_per_entry_p75
        else ""
    )
    return (
        f"Hiring screen for {role.display_name}: the draft is ONE page but only "
        f"{fill_ratio:.0%} full (target ≥{fill_target:.0%}). {band}"
        f"Current bullet count ≈ {bullet_count}.\n"
        f"Thin entries (add distinct bullets from unused facts only):\n{thin}\n\n"
        f"UNUSED INTAKE FACTS (G1-safe — you MAY turn each into a new bullet or "
        f"enrich an existing one; never invent beyond these):\n{unused}\n\n"
        "Actions allowed: add bullets from unused facts; split a dense bullet into "
        "two factual bullets; enrich education details from intake. "
        "Do NOT invent metrics, tools, or experiences. Stay within schema ceilings."
    )


def expand_elicit_system(intake: Intake) -> str:
    role = resolve_role_profile(intake.target_role)
    return f"""{hiring_persona(role)}

The current one-page draft is UNDER-FILLED (whitespace remains below skills). Ask
clarifying questions that unlock NEW distinct bullets — additional work (testing,
deployment, ownership, performance, collaboration, users/installs, security) the
applicant may have done on thin OR density-ok entries.

Rules:
- Emit 2–4 questions when the page is under-filled. Topic MUST be "expand_content".
- Prefer high impact. Target named entries and any UNUSED intake facts listed.
- Do NOT re-ask prior Q&A history.
- Set complete=true ONLY if fill is already adequate (user will say so) — if the
  fill report shows under target, complete MUST be false and questions MUST be non-empty.
- Leave id empty.

## Output JSON contract
{{
  "questions": [{{"id": "", "topic": "expand_content", "impact": "high"|"medium", "question": str, "relates_to": str}}],
  "complete": bool,
  "completion_reason": str
}}

{REFUSAL_CLAUSE}
"""


def expand_elicit_user(
    *,
    intake: Intake,
    history_block: str,
    fill_report: str,
    thin_entries: list[str],
    unused_facts: list[str] | None = None,
) -> str:
    import json

    role = resolve_role_profile(intake.target_role)
    thin = "\n".join(f"- {t}" for t in thin_entries) or (
        "(no thin entries by bullet count — still ask for more distinct work "
        "to fill remaining page whitespace)"
    )
    unused = "\n".join(f"- {u}" for u in (unused_facts or [])[:10]) or "(none left)"
    return (
        f"## Role: {role.display_name}\n"
        f"Scan first: {role.scan_first}\n\n"
        f"## Fill report\n{fill_report}\n\n"
        f"## Thin / under-used entries\n{thin}\n\n"
        f"## Unused intake facts generation failed to place (ask user to expand these)\n"
        f"{unused}\n\n"
        "## Intake (facts only)\n"
        f"{json.dumps(intake.model_dump(exclude={'answers'}), indent=2)}\n\n"
        "## Prior Q&A history (do not re-ask)\n"
        f"{history_block}\n\n"
        "Because the page is under-filled, return 2–4 expand_content questions now. "
        "complete must be false."
    )


# ---------------------------------------------------------------------------
# Elicitation
# ---------------------------------------------------------------------------

def elicit_system(intake: Intake, *, next_round: int = 1) -> str:
    role = resolve_role_profile(intake.target_role)
    round_rule = (
        "ROUND 1 REQUIREMENT: Emit 2–4 high-impact questions unless the intake is "
        "genuinely exhaustive across metrics, ownership, scope, and differentiation. "
        "Zero questions on round 1 is almost never correct — if metrics are covered, "
        "probe ownership, hardest technical decision, deployment/users, or "
        "differentiation vs tutorial projects instead."
        if next_round <= 1
        else (
            "Rounds ≥2: returning ZERO questions is valid when prior answers + intake "
            "already cover material gaps. Prefer high-impact only."
        )
    )
    return f"""{hiring_persona(role)}

You are preparing clarifying questions BEFORE rewriting this resume. Ask only what
you would need answered to shortlist for {role.display_name}.

Do NOT rewrite the resume. Do NOT invent answers.

Rules:
- Prefer questions grounded in the retrieved community critiques below. When you ask,
  name the critique theme in `relates_to` or the question text (e.g. "community flags
  unowned 'we built' claims — which modules did you personally own at SchoolTalk?").
- Metrics already written in the intake (users, leads, %, installs, etc.) are COVERED
  for that *dimension* — do NOT re-ask the same number. You MAY still ask a different
  dimension, or ownership / scope / tradeoff / differentiation questions.
- Skip trivia. Do NOT re-ask or rephrase anything in the prior Q&A history (answered,
  declined, or pending).
- Topics to prefer when metrics are already present: ownership ("which parts did you
  own?"), scope vs team, hardest technical decision, deployment/reliability, users of
  side projects, differentiation vs coursework/tutorials.
- Each question needs: topic (missing_metric|vague_scope|missing_skill|other),
  impact (high|medium), question text, and relates_to (company/project + critique theme).
  Leave id empty (the pipeline assigns a stable hash).
- {round_rule}
- Set complete=true only when further questions would not materially strengthen the
  resume for this role (rare on round 1).

## Output JSON contract
{{
  "questions": [{{"id": "", "topic": str, "impact": "high"|"medium", "question": str, "relates_to": str}}],
  "complete": bool,
  "completion_reason": str
}}

{REFUSAL_CLAUSE}
"""


def elicit_user(
    intake: Intake,
    history_block: str = "(none)",
    *,
    critiques_block: str = "(no critiques retrieved)",
) -> str:
    import json

    from src.generation.intake_coverage import format_intake_metrics_block

    return (
        "## Intake\n"
        f"{json.dumps(intake.model_dump(exclude={'answers'}), indent=2)}\n\n"
        f"{format_intake_metrics_block(intake)}\n\n"
        "## Retrieved community critiques (ground your questions in these themes)\n"
        f"{critiques_block}\n\n"
        "## Prior Q&A history (do not re-ask or rephrase any of these)\n"
        f"{history_block}\n\n"
        "Ask genuinely NEW questions a hiring reviewer from this community would ask. "
        "Do not re-ask metric dimensions already listed above. "
        "If none remain on rounds ≥2, return {\"questions\": [], \"complete\": true, "
        "\"completion_reason\": \"...\"}.\n\n"
        "Return the JSON object now."
    )


# ---------------------------------------------------------------------------
# Project evaluation
# ---------------------------------------------------------------------------

def project_eval_system(intake: Intake) -> str:
    role = resolve_role_profile(intake.target_role)
    return f"""{hiring_persona(role)}

You evaluate this applicant's PROJECT PORTFOLIO for a {role.display_name} screen.
Use ONLY the retrieved community critiques and rules provided. Every verdict MUST
cite real critique `id=` values from the retrieval block whenever any are present.
Do NOT use "rule:..." as your only evidence when critique ids exist — rules may
supplement, never replace, critique grounding.

FIRST emit portfolio_composition: classify each project domain as one of
frontend | backend | systems | ml | ai | fullstack | other. Use this to decide
field_gaps — do not claim a gap in a domain the portfolio already covers.

For each project emit:
- verdict: strong_keep | strengthen | replace
- rationale: short, grounded in a specific critique theme (name it)
- improvements: ALWAYS non-empty — even for strong_keep, say what would make it
  even stronger (metric, users, deployment, ownership clarity). Empty improvements
  are invalid.
- evidence_ids: list of critique id strings copied verbatim from the block
  (e.g. "1289087515059425341:3"). Optionally also "rule:<short>" as a supplement.

Also emit field_gaps: project TYPES the community expects for this role that the
applicant's portfolio lacks (grounded in critiques). Empty list if none evidenced.
For EVERY field gap you MUST include evidence_quote: a VERBATIM substring copied
from the retrieved critiques block (not paraphrased). Gaps without a real quote
are discarded.

CRITICAL: Copy critique `id=` values verbatim into evidence_ids. Every project
verdict MUST include ≥1 real critique id when the critiques block lists ids.
Ungrounded rows are discarded.

If a previous evaluation is provided, keep verdicts and field_gaps STABLE unless
the resume projects changed — if you change one, state exactly which change justified it.

## Output JSON contract
{{
  "portfolio_composition": [{{"name": str, "domain": "frontend"|"backend"|"systems"|"ml"|"ai"|"fullstack"|"other"}}],
  "projects": [{{
    "name": str,
    "verdict": "strong_keep"|"strengthen"|"replace",
    "rationale": str,
    "improvements": [str],
    "evidence_ids": [str]
  }}],
  "field_gaps": [{{"gap": str, "evidence_ids": [str], "evidence_quote": str}}]
}}

{G1_CLAUSE}
{REFUSAL_CLAUSE}
"""


def project_eval_user(
    *,
    projects_json: str,
    critiques_block: str,
    rules_block: str,
    role: RoleProfile,
    prior_eval_json: str | None = None,
) -> str:
    parts = [
        f"## Role: {role.display_name}",
        f"Scan first for projects: {role.scan_first}",
        "",
        "## Projects as they appear on the GENERATED resume (name, tech, bullets)",
        projects_json,
        "",
        "## Community rules for projects (MUST apply)",
        rules_block,
        "",
        "## Retrieved project critiques (MUST apply — cite ids; copy quotes verbatim)",
        critiques_block,
    ]
    if prior_eval_json:
        parts.extend(
            [
                "",
                "## Previous evaluation (keep stable unless the resume projects changed)",
                prior_eval_json,
            ]
        )
    parts.extend(
        [
            "",
            "Return the JSON evaluation now. Ungrounded claims and unquoted field gaps "
            "will be discarded.",
        ]
    )
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Critic (Phase 5) — review-community / hiring-screen persona
# ---------------------------------------------------------------------------

def critic_system(intake: Intake) -> str:
    role = resolve_role_profile(intake.target_role)
    return f"""{hiring_persona(role)}

You are now the REVIEW COMMUNITY doing a final critique pass on a resume that has
ALREADY been generated for a {role.display_name} screen. Your job is to flag
concrete weaknesses in the bullet WORDING and rule violations — not to rewrite
the resume yourself.

Use ONLY the retrieved community critiques and rules provided below. Every issue
you raise MUST cite grounding:
- critique_id: a real `id=` value copied verbatim from the critiques block, OR
- rule_id: a real rule id (the `[id]` shown in the rules block).
Issues with neither a real critique_id nor a real rule_id will be DISCARDED.

For each issue emit:
- section: experience | projects | skills | general
- entry: the company or project name the bullet belongs to (copy verbatim)
- bullet_text: the EXACT offending bullet copied verbatim from the resume JSON
- issue: what is wrong (vague scope, no metric, fluff verb, buzzword soup, etc.)
- severity: high | med | low  (high = would hurt the interview decision)
- suggested_fix: how to strengthen it — WITHOUT inventing facts. If a metric is
  missing, say "ask the applicant for X"; never invent a number (G1).
- rule_id and/or critique_id grounding.

Only raise issues you can ground. An empty issues list is a valid, honest answer
when the resume is already strong.

## Output JSON contract
{{
  "issues": [{{
    "section": str,
    "entry": str,
    "bullet_text": str,
    "issue": str,
    "severity": "high"|"med"|"low",
    "suggested_fix": str,
    "rule_id": str,
    "critique_id": str
  }}]
}}

{G1_CLAUSE}
{REFUSAL_CLAUSE}
"""


def critic_user(
    *,
    resume_json: str,
    critiques_block: str,
    rules_block: str,
    role: RoleProfile,
    gap_hints_block: str | None = None,
) -> str:
    parts = [
        f"## Role: {role.display_name}",
        f"Scan first for: {role.scan_first}",
        "",
        "## Generated resume (critique these bullets — do not rewrite them here)",
        resume_json,
        "",
        "## Community rules (cite rule ids as rule_id)",
        rules_block,
        "",
        "## Retrieved critiques against these bullets (cite id= as critique_id)",
        critiques_block,
    ]
    if gap_hints_block:
        parts.extend(
            [
                "",
                "## Bullets the writer already flagged as weak (confirm + ground, "
                "or drop if actually fine)",
                gap_hints_block,
            ]
        )
    parts.extend(
        ["", "Return the JSON issues now. Ungrounded issues will be discarded."]
    )
    return "\n".join(parts)


def revise_system(intake: Intake) -> str:
    role = resolve_role_profile(intake.target_role)
    return f"""{hiring_persona(role)}

You rewrite SPECIFIC resume bullets flagged by a reviewer, for a
{role.display_name} candidate. Rewrite ONLY the listed bullets.

Rules:
- Keep every fact identical. Do NOT invent metrics, numbers, tools, scope, or
  dates the bullet did not already contain (G1). If a metric is missing, tighten
  the wording and scope instead — never fabricate a number.
- Verb-first, concrete, no fluff adjectives.
- The `revised` text is the FINAL resume bullet. NEVER write instructions,
  meta-commentary, or the reviewer's suggested_fix into it (e.g. do NOT append
  "specify scale", "add a metric", "clarify impact"). Those are guidance for
  YOU, not text for the resume. Output only the polished bullet itself.
- Return one revision per listed bullet, echoing the exact `original` text so it
  can be matched back.

## Output JSON contract
{{"revisions": [{{"original": str, "revised": str}}]}}

{G1_CLAUSE}
{REFUSAL_CLAUSE}
"""


def critic_revise_instruction(issues: list) -> str:
    """
    Build a targeted revision instruction from critic issues.

    `issues` is a list of CriticIssue-like objects with .entry, .bullet_text,
    .issue, .suggested_fix.
    """
    lines = [
        "Rewrite ONLY the bullets listed below to address the reviewer issue. "
        "Keep every fact identical (G1 — no invented metrics, tools, or scope). "
        "Return one rewritten bullet per listed item, same order.",
        "",
    ]
    for i, it in enumerate(issues, 1):
        entry = getattr(it, "entry", "") or ""
        bullet = getattr(it, "bullet_text", "") or ""
        issue = getattr(it, "issue", "") or ""
        fix = getattr(it, "suggested_fix", "") or ""
        lines.append(f"{i}. [{entry}] bullet: {bullet!r}")
        lines.append(f"   issue: {issue}")
        if fix:
            lines.append(f"   suggested_fix: {fix}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Mining (analyst persona — extract only)
# ---------------------------------------------------------------------------

STYLE_MINE_SYSTEM = f"""You extract resume STYLE guidance from Discord critique text.

Extract ONLY what the critiques actually say. Prefer verbatim short phrases.
Do NOT invent banned words the critiques do not support.

## Output JSON contract
{{
  "banned_phrases": [
    {{"phrase": str, "example_critique": str, "thread_ids": [str]}}
  ],
  "preferred_patterns": [
    {{"pattern": str, "example_critique": str, "thread_ids": [str]}}
  ]
}}

banned_phrases = empty adjectives / filler / fluff the reviewers flag.
preferred_patterns = what they ask writers to do instead (verb-first, quantify, etc.).

{DATA_WINS_CLAUSE}
{REFUSAL_CLAUSE}
"""

REWRITE_MINE_SYSTEM = f"""You extract before→after resume rewrite pairs from critiques.

For each item, the "before" must be resume wording the critique quotes or clearly
targets. The "after" must be composed STRICTLY from the critique's own instruction
(paraphrase allowed only to form a grammatical bullet). If you cannot form a
faithful after, skip the item.

## Output JSON contract
{{
  "pairs": [
    {{
      "before": str,
      "critique_verbatim": str,
      "after": str,
      "section": str,
      "thread_id": str
    }}
  ]
}}

{DATA_WINS_CLAUSE}
{REFUSAL_CLAUSE}
"""


def style_mine_user(batch_json: str) -> str:
    return (
        "## Critique batch (extract style signals only)\n"
        f"{batch_json}\n\n"
        "Return JSON with banned_phrases and preferred_patterns grounded in these critiques."
    )


def rewrite_mine_user(batch_json: str) -> str:
    return (
        "## Critique batch (extract rewrite pairs only)\n"
        f"{batch_json}\n\n"
        "Return JSON with pairs. Skip items without a clear before quote."
    )
