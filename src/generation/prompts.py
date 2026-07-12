"""
Phase 4.6 — centralized prompt library.

Persona = judgment (big-tech hiring screen). Dataset = facts (rules, critiques,
norms, mined artifacts). Where they conflict, the data wins.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.schemas import BULLET_MAX_LEN, BULLET_MIN_LEN, Intake

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

def generator_system(intake: Intake, banned_phrases: list[str] | None = None) -> str:
    role = resolve_role_profile(intake.target_role)
    banned = banned_phrases or []
    banned_line = (
        ", ".join(banned[:40])
        if banned
        else "seamless, robust, effective, enhanced, streamlined, ensure, utilized"
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
      "company","title","dates","location",
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

Bullet constraints:
- Each bullet text length {BULLET_MIN_LEN}–{BULLET_MAX_LEN} chars.
- ≤4 bullets/experience, ≤3/project, ≤4 projects, ≤22 bullets total.
- Every bullet MUST set rewritten_from to the intake phrase it came from.
- gaps: "no_metric" when impact has no number; "vague_scope" when scope is unclear; [] when solid.
- Skills: ONLY skills the user listed (or named in project technologies). Never pad for breadth.

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
        "## Norms / skill prevalence (MUST apply for skill suggestions only)",
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


# ---------------------------------------------------------------------------
# Elicitation
# ---------------------------------------------------------------------------

def elicit_system(intake: Intake) -> str:
    role = resolve_role_profile(intake.target_role)
    return f"""{hiring_persona(role)}

You are preparing clarifying questions BEFORE rewriting this resume. Ask only what
you would need answered to shortlist for {role.display_name}.

Do NOT rewrite the resume. Do NOT invent answers.

Emit 3–8 focused questions max. Prefer metrics gaps on experience/project bullets.
Skip trivia. Each question needs a stable id (q1, q2, …), topic
(missing_metric|vague_scope|missing_skill|other), the question text, and relates_to
(company or project name + short snippet).

If the intake already has strong metrics everywhere, return {{"questions": []}}.

## Output JSON contract
{{"questions": [{{"id": str, "topic": str, "question": str, "relates_to": str}}]}}

{REFUSAL_CLAUSE}
"""


def elicit_user(intake: Intake) -> str:
    import json

    answered = set((intake.answers or {}).keys())
    return (
        "## Intake\n"
        f"{json.dumps(intake.model_dump(exclude={'answers'}), indent=2)}\n\n"
        "## Already answered question ids (do not re-ask)\n"
        f"{sorted(answered) if answered else '(none)'}\n\n"
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
cite critique_ids and/or rule statements from that context. Drop any claim you
cannot ground.

For each project emit:
- verdict: strong_keep | strengthen | replace
- rationale: short, grounded
- improvements: what to add (metrics, users, deployment) if strengthen — empty if strong_keep
- evidence_ids: list of critique id strings or "rule:<short>" markers you used

Also emit field_gaps: project TYPES the community expects for this role that the
applicant's portfolio lacks (grounded in critiques). Empty list if none evidenced.

CRITICAL: Copy critique `id=` values verbatim into evidence_ids (e.g.
"1289087515059425341:3"). You may also use "rule:<short phrase from rules block>".
Every project verdict MUST include ≥1 evidence_id. Ungrounded rows are discarded.

## Output JSON contract
{{
  "projects": [{{
    "name": str,
    "verdict": "strong_keep"|"strengthen"|"replace",
    "rationale": str,
    "improvements": [str],
    "evidence_ids": [str]
  }}],
  "field_gaps": [{{"gap": str, "evidence_ids": [str]}}]
}}

{G1_CLAUSE}
{REFUSAL_CLAUSE}
"""


def project_eval_user(
    *,
    intake_projects_json: str,
    critiques_block: str,
    rules_block: str,
    role: RoleProfile,
) -> str:
    return (
        f"## Role: {role.display_name}\n"
        f"Scan first for projects: {role.scan_first}\n\n"
        "## Applicant projects (facts only)\n"
        f"{intake_projects_json}\n\n"
        "## Community rules for projects (MUST apply)\n"
        f"{rules_block}\n\n"
        "## Retrieved project critiques (MUST apply — cite ids)\n"
        f"{critiques_block}\n\n"
        "Return the JSON evaluation now. Ungrounded claims will be discarded."
    )


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
