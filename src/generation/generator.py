"""
Phase 4 — resume content generator (gpt-4o + rules + retrieve + norms + G1).

Never invents skills/tools/metrics the user did not provide. High-prevalence
skills absent from intake go into `suggestions`, not the resume.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from src.knowledge.retrieve import format_for_prompt, retrieve
from src.llm import MODEL_SYNTHESIS, complete_json
from src.schemas import (
    BULLET_MAX_LEN,
    BULLET_MIN_LEN,
    GenerationResult,
    Intake,
    ResumeContent,
    Suggestion,
)

DEFAULT_RULEBOOK = Path("data/knowledge/rulebook.json")
DEFAULT_NORMS = Path("data/norms/norms.json")
HIGH_PREVALENCE = 0.50
MAX_RULES = 20
RETRIEVE_SECTIONS = ("education", "experience", "projects", "skills")
RETRIEVE_K = 5

G1_GUARDRAIL = (
    "G1 — No fabrication. NEVER invent skills, tools, metrics, numbers, dates, "
    "titles, or experiences the user did not provide. Gaps are surfaced as "
    "suggestions in the report, never silently added to the resume."
)

SYSTEM_PROMPT = f"""You are an expert CS resume writer grounded in community review data \
from a Discord resume-critique channel.

{G1_GUARDRAIL}

Output a single JSON object with this shape:
{{
  "resume": {{
    "contact": {{"name": str, "email": str, "phone": str, "linkedin": str, "github": str, "website": str}},
    "education": [{{"school": str, "degree": str, "dates": str, "location": str, "details": str}}],
    "experience": [{{"company": str, "title": str, "dates": str, "location": str, "bullets": [str, ...]}}],
    "projects": [{{"name": str, "technologies": str, "dates": str, "bullets": [str, ...]}}],
    "skills": {{"Languages": [str], "Frameworks": [str], "Developer Tools": [str], "Libraries": [str]}},
    "section_order": ["education", "experience", "projects", "skills"]
  }},
  "suggestions": [{{"type": "missing_skill|missing_metric|content_gap", "detail": str}}]
}}

Bullet constraints (strict):
- Each bullet length must be between {BULLET_MIN_LEN} and {BULLET_MAX_LEN} characters.
- ≤4 bullets per experience entry, ≤3 per project, ≤4 projects, ≤22 bullets total.
- Prefer quantified impact ONLY when the intake provides numbers; otherwise write strong \
action bullets without inventing metrics.
- Skills section: ONLY list skills the user explicitly provided (or clearly named in \
their project technologies). Do not add high-prevalence community skills they omitted — \
put those in suggestions as type "missing_skill".
"""


def load_rulebook(path: Path = DEFAULT_RULEBOOK) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_norms(path: Path = DEFAULT_NORMS) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def intake_norms_bucket(intake: Intake) -> str:
    """Map intake role/year/internships to a Phase-2 norms bucket key."""
    role_l = (intake.target_role or "").lower()
    year = (intake.year or "").lower() or None
    is_intern = bool(intake.has_internships) or (
        year is not None and year.startswith("year_")
    )
    is_new_grad = year == "new_grad"

    if "data" in role_l and "scientist" in role_l:
        return "data_intern" if is_intern and not is_new_grad else "data"
    if "machine learning" in role_l or role_l.startswith("ml"):
        return "ml"
    if (
        "software" in role_l
        or "engineer" in role_l
        or "full stack" in role_l
        or "frontend" in role_l
        or "backend" in role_l
    ):
        if is_intern and not is_new_grad:
            return "swe_intern"
        if is_new_grad:
            return "swe_new_grad"
        return "swe"
    if "data" in role_l:
        return "data_intern" if is_intern and not is_new_grad else "data"
    return "swe_intern" if is_intern else "swe"


def applicable_rules(
    rulebook: dict[str, Any],
    intake: Intake,
    cap: int = MAX_RULES,
) -> list[dict[str, Any]]:
    """Filter rules by role/profile, order by frequency, cap at `cap`."""
    role = intake.target_role
    year = intake.year or ""
    out: list[dict[str, Any]] = []
    for rule in rulebook.get("rules", []):
        applies = rule.get("applies_to") or ["all"]
        applies_l = [str(a).lower() for a in applies]
        if (
            "all" in applies_l
            or role.lower() in applies_l
            or any(role.lower() in a or a in role.lower() for a in applies_l)
            or (year and year.lower() in applies_l)
            or ("intern" in applies_l and intake.has_internships)
        ):
            out.append(rule)
    out.sort(key=lambda r: -float(r.get("frequency", 0)))
    return out[:cap]


def format_rules_block(rules: list[dict[str, Any]]) -> str:
    if not rules:
        return "(no applicable rules)"
    lines = []
    for i, r in enumerate(rules, 1):
        lines.append(
            f"{i}. [{r.get('category')}/{r.get('section')}] "
            f"(freq={r.get('frequency')}) {r.get('statement')}"
        )
    return "\n".join(lines)


def skill_prevalence_for_intake(
    norms: dict[str, Any],
    intake: Intake,
) -> dict[str, float]:
    bucket = intake_norms_bucket(intake)
    entry = (norms.get("roles") or {}).get(bucket) or {}
    if entry.get("insufficient_data"):
        # Fall back to largest SWE-family bucket with data.
        for fb in ("swe_intern", "swe", "swe_new_grad"):
            alt = (norms.get("roles") or {}).get(fb) or {}
            if alt and not alt.get("insufficient_data"):
                return dict(alt.get("skill_prevalence") or {})
        return {}
    return dict(entry.get("skill_prevalence") or {})


def format_norms_block(
    prevalence: dict[str, float],
    intake_skills: list[str],
    threshold: float = HIGH_PREVALENCE,
) -> str:
    owned = {_norm_skill(s) for s in intake_skills}
    lines = [
        "Skill prevalence for the applicant's role bucket "
        f"(threshold for 'high' = {threshold:.0%}):",
    ]
    if not prevalence:
        lines.append("(no norms available)")
        return "\n".join(lines)

    high = [(s, p) for s, p in prevalence.items() if p > threshold]
    high.sort(key=lambda x: -x[1])
    for skill, prev in high[:25]:
        flag = "PRESENT" if _norm_skill(skill) in owned else "ABSENT — suggest only, do NOT add"
        lines.append(f"- {skill}: {prev:.1%} [{flag}]")

    lines.append(
        "\nIf a high-prevalence skill (>50%) is ABSENT from the user's skills, "
        "DO NOT add it to the resume. Append a suggestions entry "
        '{"type": "missing_skill", "detail": "..."} instead.'
    )
    return "\n".join(lines)


def _norm_skill(s: str) -> str:
    # Strip punctuation/spaces so "Node.js" and "Nodejs" match.
    return re.sub(r"[^a-z0-9+#]+", "", s.lower())


def allowed_skills(intake: Intake) -> set[str]:
    """Canonical skill tokens the resume may list."""
    tokens: set[str] = set()
    for s in intake.skills:
        tokens.add(_norm_skill(s))
    for proj in intake.projects:
        for part in re.split(r"[,|/]", proj.technologies or ""):
            part = part.strip()
            if part:
                tokens.add(_norm_skill(part))
    return {t for t in tokens if t}


def intake_text_blob(intake: Intake) -> str:
    parts = [
        intake.profile_summary,
        " ".join(intake.skills),
    ]
    for e in intake.education:
        parts.append(f"{e.school} {e.degree} {e.details}")
    for x in intake.experience:
        parts.append(f"{x.company} {x.title} {x.description}")
    for p in intake.projects:
        parts.append(f"{p.name} {p.technologies} {p.description}")
    return " ".join(parts).lower()


def retrieve_context(intake: Intake, k: int = RETRIEVE_K) -> str:
    profile = intake.to_applicant_profile()
    blocks: list[str] = []
    query_base = (
        f"{intake.target_role} {intake.profile_summary} "
        f"{' '.join(intake.skills[:12])}"
    )
    for section in RETRIEVE_SECTIONS:
        points = retrieve(profile, section, query_base, k=k)
        blocks.append(f"### Critiques — {section}\n{format_for_prompt(points)}")
    return "\n\n".join(blocks)


def format_intake_block(intake: Intake) -> str:
    return json.dumps(intake.model_dump(), indent=2)


def build_generation_prompt(
    intake: Intake,
    *,
    rulebook: dict[str, Any] | None = None,
    norms: dict[str, Any] | None = None,
    trim_instruction: str | None = None,
    rulebook_path: Path = DEFAULT_RULEBOOK,
    norms_path: Path = DEFAULT_NORMS,
) -> str:
    rulebook = rulebook if rulebook is not None else load_rulebook(rulebook_path)
    norms = norms if norms is not None else load_norms(norms_path)
    rules = applicable_rules(rulebook, intake)
    prevalence = skill_prevalence_for_intake(norms, intake)

    parts = [
        "## Applicant intake (source of truth — do not invent beyond this)",
        format_intake_block(intake),
        "",
        "## Applicable community rules",
        format_rules_block(rules),
        "",
        "## Retrieved community critiques",
        retrieve_context(intake),
        "",
        "## Norms / skill prevalence",
        format_norms_block(prevalence, intake.skills),
    ]
    if trim_instruction:
        parts.extend(["", "## Page-fit trim instruction", trim_instruction])
    parts.append(
        "\nReturn the JSON object now. Remember G1: suggestions for gaps, never silent adds."
    )
    return "\n".join(parts)


def enforce_g1(
    result: GenerationResult,
    intake: Intake,
    prevalence: dict[str, float],
    threshold: float = HIGH_PREVALENCE,
) -> GenerationResult:
    """
    Deterministic post-process: strip fabricated skills; ensure high-prevalence
    absences appear in suggestions.
    """
    allowed = allowed_skills(intake)
    cleaned_skills: dict[str, list[str]] = {}
    removed: list[str] = []
    for cat, items in (result.resume.skills or {}).items():
        kept: list[str] = []
        for item in items:
            if _norm_skill(item) in allowed:
                kept.append(item)
            else:
                removed.append(item)
        if kept:
            cleaned_skills[cat] = kept

    suggestions = list(result.suggestions)
    owned = {_norm_skill(s) for s in intake.skills}
    existing_missing = {
        _norm_skill(s.detail)
        for s in suggestions
        if s.type == "missing_skill"
    }

    for skill, prev in sorted(prevalence.items(), key=lambda x: -x[1]):
        if prev <= threshold:
            continue
        key = _norm_skill(skill)
        if key in owned:
            continue
        # Already covered?
        if any(key in _norm_skill(s.detail) or skill.lower() in s.detail.lower() for s in suggestions):
            continue
        if key in existing_missing:
            continue
        suggestions.append(
            Suggestion(
                type="missing_skill",
                detail=(
                    f"{skill} appears on {prev:.0%} of similar resumes in the community "
                    f"corpus but was not listed in your skills. Consider adding it only "
                    f"if you actually use it — do not claim it otherwise."
                ),
            )
        )
        existing_missing.add(key)

    for skill in removed:
        if not any(skill.lower() in s.detail.lower() for s in suggestions):
            suggestions.append(
                Suggestion(
                    type="missing_skill",
                    detail=(
                        f"Removed fabricated skill {skill!r} from the resume (G1). "
                        f"Add it to intake only if you actually have it."
                    ),
                )
            )

    resume = result.resume.model_copy(update={"skills": cleaned_skills})
    # Re-validate via ResumeContent constructors already done; rebuild result.
    return GenerationResult(resume=resume, suggestions=suggestions)


def generate_resume(
    intake: Intake,
    *,
    trim_instruction: str | None = None,
    rulebook_path: Path = DEFAULT_RULEBOOK,
    norms_path: Path = DEFAULT_NORMS,
    model: str = MODEL_SYNTHESIS,
    phase: str = "phase4",
) -> GenerationResult:
    """One synthesis call → GenerationResult, then G1 enforcement."""
    rulebook = load_rulebook(rulebook_path)
    norms = load_norms(norms_path)
    prevalence = skill_prevalence_for_intake(norms, intake)
    prompt = build_generation_prompt(
        intake,
        rulebook=rulebook,
        norms=norms,
        trim_instruction=trim_instruction,
        rulebook_path=rulebook_path,
        norms_path=norms_path,
    )
    result = complete_json(
        prompt=prompt,
        model=model,
        phase=phase,
        schema=GenerationResult,
        system=SYSTEM_PROMPT,
        max_tokens=8192,
    )
    return enforce_g1(result, intake, prevalence)
