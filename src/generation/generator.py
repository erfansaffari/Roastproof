"""
Phase 4 / 4.5 / 4.6 — resume content generator.

Facts are frozen; wording is mandatory to rewrite. Knowledge comes from
rulebook + retrieve + norms + mined style/rewrite artifacts (not hardcoding).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

from src.generation.fluff import (
    banned_phrase_set,
    fluff_retry_instruction,
    lint_resume_fluff,
    preferred_patterns,
)
from src.generation.prompts import (
    generator_system,
    generator_user_prompt,
    resolve_role_profile,
)
from src.knowledge.retrieve import format_for_prompt, retrieve
from src.knowledge.rewrite_mine import format_pairs_for_prompt, load_rewrite_examples
from src.llm import MODEL_SYNTHESIS, complete_json
from src.schemas import (
    AnnotatedGenerationResult,
    AnnotatedResume,
    BULLET_MAX_LEN,
    BULLET_MIN_LEN,
    GenerationResult,
    Intake,
    ResumeContent,
    Suggestion,
)

DEFAULT_RULEBOOK = Path("data/knowledge/rulebook.json")
DEFAULT_NORMS = Path("data/norms/norms.json")
DEFAULT_REWRITE_EXAMPLES = Path("data/knowledge/rewrite_examples.json")
HIGH_PREVALENCE = 0.50
COMMON_PREVALENCE = 0.25
MAX_RULES = 20
RETRIEVE_SECTIONS = ("education", "experience", "projects", "skills")
RETRIEVE_K = 5

_FALLBACK_FEW_SHOTS = """
Weak: "Optimized platform features by refactoring backend code in Node.js, reducing loading times and enhancing UX."
Critique: empty optimized/enhancing; metric vague.
Strong: "Refactored Node.js API handlers for the checkout path, cutting p95 page load from 1.8s to 1.4s (-23%)."

Weak: "Built a robust RBAC system to ensure data integrity across tenants."
Critique: robust/ensure are fluff; no scope.
Strong: "Designed tenant-scoped RBAC (roles, permissions, school isolation) so each school's data stays partitioned."
"""


def load_rulebook(path: Path = DEFAULT_RULEBOOK) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_norms(path: Path = DEFAULT_NORMS) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def intake_norms_bucket(intake: Intake) -> str:
    role_l = (intake.target_role or "").lower()
    year = (intake.year or "").lower() or None
    is_intern = bool(intake.has_internships) or (
        year is not None and year.startswith("year_")
    )
    is_new_grad = year == "new_grad"

    if "data" in role_l and "scientist" in role_l:
        return "data_intern" if is_intern and not is_new_grad else "data"
    if "machine learning" in role_l or role_l.startswith("ml") or "ai eng" in role_l:
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
) -> tuple[dict[str, float], str, bool]:
    bucket = intake_norms_bucket(intake)
    entry = (norms.get("roles") or {}).get(bucket) or {}
    if entry.get("insufficient_data") or not entry:
        for fb in ("swe_intern", "swe", "swe_new_grad"):
            alt = (norms.get("roles") or {}).get(fb) or {}
            if alt and not alt.get("insufficient_data"):
                return dict(alt.get("skill_prevalence") or {}), fb, True
        return {}, bucket, True
    return dict(entry.get("skill_prevalence") or {}), bucket, False


def norms_entry_for_intake(
    norms: dict[str, Any],
    intake: Intake,
) -> tuple[dict[str, Any], str, bool]:
    """Return (role entry, bucket, thin) for bullet-density targets."""
    bucket = intake_norms_bucket(intake)
    entry = (norms.get("roles") or {}).get(bucket) or {}
    thin = bool(entry.get("insufficient_data") or not entry)
    if thin:
        for fb in ("swe_intern", "swe", "swe_new_grad"):
            alt = (norms.get("roles") or {}).get(fb) or {}
            if alt and not alt.get("insufficient_data"):
                return dict(alt), fb, True
        return dict(entry), bucket, True
    return dict(entry), bucket, False


def format_bullet_targets(entry: dict[str, Any], *, bucket: str = "") -> str:
    med = entry.get("median_bullets_per_entry")
    p75 = entry.get("bullets_per_entry_p75")
    tmed = entry.get("total_bullets_median")
    tp75 = entry.get("total_bullets_p75")
    if med is None and p75 is None:
        return f"bucket `{bucket or 'unknown'}` — no bullet-density norms yet"
    parts = [f"bucket `{bucket or 'unknown'}`"]
    if med is not None:
        parts.append(f"median {float(med):.1f} bullets/entry")
    if p75 is not None:
        parts.append(f"upper band (p75) {float(p75):.1f} bullets/entry")
    if tmed is not None:
        parts.append(f"typical total ≈ {float(tmed):.0f}")
    if tp75 is not None:
        parts.append(f"upper-band total ≈ {float(tp75):.0f}")
    return (
        "; ".join(parts)
        + " — target the upper band when intake material allows"
    )


def _norm_skill(s: str) -> str:
    return re.sub(r"[^a-z0-9+#]+", "", s.lower())


def expand_skill_tokens(skills: Iterable[str]) -> set[str]:
    tokens: set[str] = set()
    for raw in skills:
        if not raw or not str(raw).strip():
            continue
        s = str(raw).strip()
        whole = _norm_skill(s)
        if whole:
            tokens.add(whole)
        for part in re.split(r"[/,|&]|\\band\\b", s, flags=re.I):
            part = part.strip()
            if not part:
                continue
            n = _norm_skill(part)
            if n:
                tokens.add(n)
            for sub in re.split(r"\s+", part):
                sn = _norm_skill(sub)
                if sn and len(sn) >= 2:
                    tokens.add(sn)
    return tokens


def skill_is_owned(skill: str, owned: set[str]) -> bool:
    key = _norm_skill(skill)
    if not key:
        return False
    if key in owned:
        return True
    for o in owned:
        if key in o or o in key:
            if min(len(key), len(o)) <= 2 and key != o:
                continue
            return True
    return False


def owned_skills_from_intake(intake: Intake) -> set[str]:
    parts: list[str] = list(intake.skills)
    for exp in intake.experience:
        if getattr(exp, "technologies", None):
            parts.append(exp.technologies)
    for proj in intake.projects:
        if proj.technologies:
            parts.append(proj.technologies)
    return expand_skill_tokens(parts)


def owned_skills_from_resume(resume: ResumeContent | AnnotatedResume) -> set[str]:
    flat: list[str] = []
    skills = resume.skills or {}
    for items in skills.values():
        flat.extend(items or [])
    return expand_skill_tokens(flat)


def format_norms_block(
    prevalence: dict[str, float],
    owned: set[str],
    *,
    bucket: str = "",
    thin: bool = False,
    core_threshold: float = HIGH_PREVALENCE,
    common_threshold: float = COMMON_PREVALENCE,
    norms_entry: dict[str, Any] | None = None,
) -> str:
    lines = [
        f"Skill prevalence for role bucket `{bucket or 'unknown'}` "
        f"(core >{core_threshold:.0%}, common >{common_threshold:.0%}). "
        "PRESENT means the applicant already has this skill (possibly as a compound "
        "like Git/GitHub Actions). ABSENT → suggest only with prevalence; never add.",
    ]
    if norms_entry:
        lines.append(
            "Bullet density (community): "
            + format_bullet_targets(norms_entry, bucket=bucket)
        )
    if thin:
        lines.append(
            "DISCLOSURE: primary bucket was thin/insufficient; figures may be from "
            "a SWE-family fallback. Do not overstate certainty."
        )
    if not prevalence:
        lines.append("(no skill prevalence available)")
        return "\n".join(lines)

    core = [(s, p) for s, p in prevalence.items() if p > core_threshold]
    common = [
        (s, p)
        for s, p in prevalence.items()
        if common_threshold < p <= core_threshold
    ]
    core.sort(key=lambda x: -x[1])
    common.sort(key=lambda x: -x[1])

    lines.append("### Core (>50%)")
    for skill, prev in core[:25]:
        flag = "PRESENT" if skill_is_owned(skill, owned) else "ABSENT — suggest only"
        lines.append(f"- {skill}: {prev:.1%} [{flag}]")
    lines.append("### Common (25–50%)")
    for skill, prev in common[:20]:
        flag = "PRESENT" if skill_is_owned(skill, owned) else "ABSENT — suggest only"
        lines.append(f"- {skill}: {prev:.1%} [{flag}]")

    lines.append(
        "\nNever suggest a skill marked PRESENT. Cite prevalence % and say "
        "add only if they know it — never for breadth/industry standards."
    )
    return "\n".join(lines)


def allowed_skills(intake: Intake) -> set[str]:
    return owned_skills_from_intake(intake)


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


def format_answers_block(intake: Intake, qa_store=None) -> str:
    """Render elicitation answers for the generator prompt.

    Prefer the QA sidecar (full Q→A pairs). Fall back to legacy intake.answers.
    """
    if qa_store is not None and getattr(qa_store, "questions", None):
        from src.generation.qa_store import format_answers_block_from_store

        return format_answers_block_from_store(qa_store)
    if not intake.answers:
        return "(no answers yet — do not invent the missing facts)"
    lines = ["User-provided answers to elicitation questions (treat as facts):"]
    for qid, ans in intake.answers.items():
        lines.append(f"- {qid}: {ans}")
    return "\n".join(lines)


def few_shot_block(rewrite_path: Path = DEFAULT_REWRITE_EXAMPLES) -> str:
    pairs = load_rewrite_examples(rewrite_path)
    if pairs:
        return format_pairs_for_prompt(pairs, k=4)
    return _FALLBACK_FEW_SHOTS.strip()


def build_generation_prompt(
    intake: Intake,
    *,
    rulebook: dict[str, Any] | None = None,
    norms: dict[str, Any] | None = None,
    trim_instruction: str | None = None,
    fluff_instruction: str | None = None,
    expand_instruction: str | None = None,
    rulebook_path: Path = DEFAULT_RULEBOOK,
    norms_path: Path = DEFAULT_NORMS,
    rewrite_path: Path = DEFAULT_REWRITE_EXAMPLES,
    qa_store=None,
) -> str:
    rulebook = rulebook if rulebook is not None else load_rulebook(rulebook_path)
    norms = norms if norms is not None else load_norms(norms_path)
    rules = applicable_rules(rulebook, intake)
    prevalence, bucket, thin = skill_prevalence_for_intake(norms, intake)
    norms_entry, _, _ = norms_entry_for_intake(norms, intake)
    owned = owned_skills_from_intake(intake)
    role = resolve_role_profile(intake.target_role)

    return generator_user_prompt(
        few_shots=few_shot_block(rewrite_path),
        rules_block=format_rules_block(rules),
        critiques_block=retrieve_context(intake),
        norms_block=format_norms_block(
            prevalence,
            owned,
            bucket=bucket,
            thin=thin,
            norms_entry=norms_entry,
        ),
        answers_block=format_answers_block(intake, qa_store=qa_store),
        intake_block=format_intake_block(intake),
        role=role,
        trim_instruction=trim_instruction,
        fluff_instruction=fluff_instruction,
        expand_instruction=expand_instruction,
        preferred_patterns=preferred_patterns(),
    )


def fit_bullet_length(text: str) -> str | None:
    """
    Fit a bullet into [BULLET_MIN_LEN, BULLET_MAX_LEN].

    Too long → truncate at a word boundary (no invented words).
    Too short → return None (caller drops it).
    """
    t = (text or "").strip()
    if not t:
        return None
    if len(t) > BULLET_MAX_LEN:
        cut = t[:BULLET_MAX_LEN]
        if " " in cut:
            cut = cut.rsplit(" ", 1)[0].rstrip(" ,;:-")
        t = cut
        if not t.endswith((".", "!", "?")):
            t = t + "."
        if len(t) > BULLET_MAX_LEN:
            t = t[:BULLET_MAX_LEN]
    if len(t) < BULLET_MIN_LEN:
        if len(t) == BULLET_MIN_LEN - 1 and not t.endswith((".", "!", "?")):
            t = t + "."
        if len(t) < BULLET_MIN_LEN:
            return None
    return t


def ground_technologies(tech_line: str, attested_blob: str) -> str:
    """
    Keep only comma/middot-separated tech tokens attested in `attested_blob` (G1).

    A token is kept if its normalized form appears in the attested text's skill
    tokens, or as a substring of the raw attested blob (case-insensitive).
    """
    if not (tech_line or "").strip():
        return ""
    blob = attested_blob or ""
    blob_l = blob.lower()
    attested_tokens = expand_skill_tokens([blob])
    kept: list[str] = []
    # Split on commas or middot-like separators the model may emit
    parts = re.split(r"\s*[,·|/]\s*|\s+\\?\\?cdot\s+", tech_line)
    for part in parts:
        part = part.strip().strip(".")
        if not part:
            continue
        # Strip LaTeX middot leftovers if any
        part = re.sub(r"\$\\cdot\$", "", part).strip()
        if not part:
            continue
        key = _norm_skill(part)
        if not key:
            continue
        if key in attested_tokens or skill_is_owned(part, attested_tokens):
            kept.append(part)
            continue
        # Substring fallback for multi-word tools already written in prose
        if part.lower() in blob_l or key in _norm_skill(blob):
            kept.append(part)
            continue
        # Soft match: any attested token contained in part or vice versa
        if any(
            (key in o or o in key) and min(len(key), len(o)) >= 3
            for o in attested_tokens
        ):
            kept.append(part)
    # Dedupe preserving order
    seen: set[str] = set()
    out: list[str] = []
    for k in kept:
        nk = _norm_skill(k)
        if nk in seen:
            continue
        seen.add(nk)
        out.append(k)
    return ", ".join(out)


_PLUS_CHAIN = re.compile(
    r"((?:[A-Za-z0-9][\w.#/-]*)(?:\s+[A-Za-z][\w.#/-]*){0,2}"
    r"(?:\s*\+\s*[A-Za-z0-9][\w.#/-]*(?:\s+[A-Za-z][\w.#/-]*){0,2}){1,6})"
)
_AND_STACK = re.compile(
    r"\b(?:in|with|using|via)\s+"
    r"([A-Za-z][\w.+#/-]*(?:\s+[A-Za-z][\w.+#/-]*){0,2})"
    r"\s+and\s+"
    r"([A-Za-z][\w.+#/-]*(?:\s+[A-Za-z][\w.+#/-]*){0,2})",
    re.I,
)
_TRAILING_PROSE = frozenset(
    {
        "pipeline",
        "pipelines",
        "dashboard",
        "system",
        "systems",
        "platform",
        "app",
        "application",
        "service",
        "services",
        "agent",
        "tooling",
        "stack",
        "backend",
        "frontend",
        "tables",
        "table",
        "region",
        "fallback",
    }
)
_LEADING_PROSE = frozenset(
    {
        "an", "a", "the", "our", "their", "and", "or", "to", "for", "from",
        "with", "using", "via", "in", "on", "of", "as", "by", "built", "shipped",
        "automated", "support", "combining",
    }
)
_PROSE_STOP = frozenset(
    {
        "leads", "lead", "clients", "client", "users", "user", "inquiries",
        "inquiry", "weekly", "monthly", "qualified", "commercial", "across",
        "without", "per", "queries", "query", "wired", "directly", "giving",
        "cutting", "handling", "generating", "producing", "reducing", "active",
        "schools", "school", "team", "sales", "response", "time", "times",
        "requests", "request", "visibility", "campaigns", "campaign",
    }
)
_REJECT_SOLO = frozenset(
    {
        "ai", "api", "apis", "gpt", "gta", "saas", "linkedin", "montreal",
        "toronto", "waterloo", "remote", "llm", "ml", "rpc", "tcp", "http",
        "https", "json", "yaml", "csv", "pdf", "cli", "ui", "ux", "db",
    }
)


def technology_vocab_from_intake(intake: Intake) -> list[str]:
    """Candidate tech names from skills + any technologies fields on intake."""
    vocab: list[str] = list(intake.skills or [])
    for exp in intake.experience or []:
        if getattr(exp, "technologies", None):
            vocab.extend(re.split(r"\s*,\s*", exp.technologies))
    for proj in intake.projects or []:
        if proj.technologies:
            vocab.extend(re.split(r"\s*,\s*", proj.technologies))
    # Dedupe preserving order (longer names first helps matching)
    seen: set[str] = set()
    out: list[str] = []
    for v in sorted((x.strip() for x in vocab if x and str(x).strip()), key=len, reverse=True):
        k = _norm_skill(v)
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(v)
    return out


def infer_technologies_from_blob(blob: str, vocab: list[str]) -> str:
    """
    Deterministic G1-safe tech line: vocab items attested in blob, plus tools
    appearing in ``A + B + C`` / ``X and Y`` stacks in the same prose.
    """
    if not (blob or "").strip():
        return ""
    blob_l = blob.lower()
    found: list[str] = []
    seen: set[str] = set()
    vocab_norm = {_norm_skill(v): v for v in vocab if v}

    def _add(name: str, *, require_toolish: bool = False) -> None:
        name = (name or "").strip(" .,;:")
        if not name or len(name) < 2 or len(name) > 48:
            return
        words = name.split()
        while words and words[0].lower().strip(".,") in _LEADING_PROSE:
            words = words[1:]
        while len(words) > 1 and words[-1].lower().strip(".,") in _TRAILING_PROSE:
            words = words[:-1]
        name = " ".join(words).strip(" .,;:")
        if not name or len(name) < 2:
            return
        low = name.lower()
        if low in _TRAILING_PROSE or low in _LEADING_PROSE:
            return
        if low in _REJECT_SOLO:
            return
        # Pure metrics / counts are not technologies
        if re.fullmatch(r"[\d,+.%]+", name):
            return
        if any(w.lower().strip(".,") in _PROSE_STOP for w in words):
            return
        # Drop sentence fragments (period+space), not tool names like Next.js
        if re.search(r"\.\s", name) or name.endswith("...") or len(words) > 4:
            return
        key = _norm_skill(name)
        if not key or key in seen:
            return
        if require_toolish:
            # Must look like a tool: capital letter, digit mix (n8n), or known vocab
            if key not in vocab_norm and not (
                re.search(r"[A-Z]", name) or re.search(r"[a-z]+\d|\d+[a-z]", low)
            ):
                return
        seen.add(key)
        found.append(vocab_norm.get(key, name))

    for v in vocab:
        if v.lower() in blob_l:
            _add(v, require_toolish=False)

    # Normalize spaced pluses so "n8n + OpenAI API + Resend" parses cleanly
    plus_blob = re.sub(r"\s*\+\s*", " + ", blob)
    for m in _PLUS_CHAIN.finditer(plus_blob):
        for part in m.group(1).split(" + "):
            _add(part, require_toolish=True)

    for m in _AND_STACK.finditer(blob):
        _add(m.group(1), require_toolish=True)
        _add(m.group(2), require_toolish=True)

    # Mid-sentence TitleCase / CamelCase tokens (Convex, Clerk, Gemini, …)
    for m in re.finditer(r"(?<=\s)([A-Z][A-Za-z0-9+#]*(?:\.[A-Za-z]+)?)\b", blob):
        tok = m.group(1)
        if tok.lower() in _PROSE_STOP | _LEADING_PROSE | _TRAILING_PROSE | _REJECT_SOLO:
            continue
        if tok in {
            "Canadian", "President", "Mathematics", "International", "Students",
            "Entrance", "Scholarship", "Remote", "Bachelor", "Computer", "Science",
            "Founded", "Built", "Architected", "Shipped", "Implemented", "Designed",
            "Created", "Developed", "Established", "Crafted",
        }:
            continue
        _add(tok, require_toolish=True)

    # Drop shorter tokens already covered by a longer one ("OpenAI" vs "OpenAI API")
    compact: list[str] = []
    norms = [_norm_skill(x) for x in found]
    for i, name in enumerate(found):
        ni = norms[i]
        if any(i != j and ni != nj and ni in nj for j, nj in enumerate(norms)):
            continue
        compact.append(name)
    return ", ".join(compact)


def resolve_entry_technologies(
    llm_tech: str,
    attested_blob: str,
    vocab: list[str],
    *,
    infer_from: str | None = None,
) -> str:
    """Prefer grounded LLM tech; if empty, infer from intake prose (not bullets)."""
    grounded = ground_technologies(llm_tech or "", attested_blob)
    if grounded:
        return grounded
    source = infer_from if infer_from is not None else attested_blob
    inferred = infer_technologies_from_blob(source, vocab)
    return ground_technologies(inferred, attested_blob) if inferred else ""


def entry_attested_blob(
    intake: Intake,
    *,
    company: str | None = None,
    project_name: str | None = None,
    qa_store=None,
) -> str:
    """Concatenate intake text + related QA answers that ground an entry's tech line."""
    parts: list[str] = []
    if company:
        for exp in intake.experience:
            if exp.company == company:
                if exp.technologies:
                    parts.append(exp.technologies)
                parts.append(exp.description or "")
                break
    if project_name:
        for proj in intake.projects:
            if proj.name == project_name:
                if proj.technologies:
                    parts.append(proj.technologies)
                parts.append(proj.description or "")
                break
    needle = (company or project_name or "").lower()
    if qa_store is not None and needle:
        for q in getattr(qa_store, "questions", []) or []:
            if q.status != "answered" or not q.answer:
                continue
            relates = (q.relates_to or "").lower()
            if needle in relates or relates in needle:
                parts.append(q.answer)
                parts.append(q.relates_to or "")
    return "\n".join(parts)


def annotated_to_resume(annotated: AnnotatedResume) -> ResumeContent:
    experience = []
    for e in annotated.experience:
        bullets = [fit_bullet_length(b.text) for b in e.bullets]
        bullets = [b for b in bullets if b]
        if not bullets:
            # Keep at least something so the entry isn't empty — use longest original clipped
            raw = max((b.text for b in e.bullets), key=len, default="")
            repaired = fit_bullet_length(raw) or (raw[:BULLET_MAX_LEN] if raw else None)
            if repaired and len(repaired) >= BULLET_MIN_LEN:
                bullets = [repaired]
            elif raw:
                # Last resort: hard-pad is forbidden; skip entry bullets and let validator
                # fail only if everything empty — use model_construct path via soft pad of
                # spaces is bad. Repeat last chars? No. Use ellipsis extension from tools in name.
                # Prefer dropping the entry's short bullets entirely if none fit.
                bullets = []
        experience.append(
            {
                "company": e.company,
                "title": e.title,
                "dates": e.dates,
                "location": e.location,
                "technologies": getattr(e, "technologies", "") or "",
                "bullets": bullets,
            }
        )
    projects = []
    for p in annotated.projects:
        bullets = [fit_bullet_length(b.text) for b in p.bullets]
        bullets = [b for b in bullets if b]
        projects.append(
            {
                "name": p.name,
                "technologies": p.technologies,
                "dates": p.dates,
                "bullets": bullets,
            }
        )
    # Drop experience/project entries that ended with zero bullets after repair
    experience = [e for e in experience if e.get("bullets")]
    projects = [p for p in projects if p.get("bullets")]
    return ResumeContent.model_validate(
        {
            "contact": annotated.contact,
            "education": annotated.education,
            "experience": experience,
            "projects": projects,
            "skills": annotated.skills,
            "section_order": annotated.section_order,
        }
    )


def suggestions_from_bullet_gaps(
    annotated: AnnotatedResume,
    *,
    declined_needles: list[str] | None = None,
) -> list[Suggestion]:
    """Build gap suggestions; suppress missing_metric when user declined that topic."""
    needles = [n.lower() for n in (declined_needles or []) if n]

    def _declined_for(detail: str) -> bool:
        d = detail.lower()
        return any(n and n in d for n in needles)

    out: list[Suggestion] = []
    for e in annotated.experience:
        for b in e.bullets:
            if "no_metric" in (b.gaps or []):
                detail = (
                    f"[{e.company}] Bullet has no quantified impact: "
                    f"{b.text[:100]}{'…' if len(b.text) > 100 else ''}. "
                    f"Add a real number in intake answers if you have one."
                )
                if not _declined_for(f"{e.company} {b.text} {b.rewritten_from}"):
                    out.append(Suggestion(type="missing_metric", detail=detail))
            if "vague_scope" in (b.gaps or []):
                out.append(
                    Suggestion(
                        type="content_gap",
                        detail=(
                            f"[{e.company}] Scope still vague after rewrite: "
                            f"{b.text[:100]}{'…' if len(b.text) > 100 else ''}."
                        ),
                    )
                )
    for p in annotated.projects:
        for b in p.bullets:
            if "no_metric" in (b.gaps or []):
                detail = (
                    f"[{p.name}] Bullet has no quantified impact: "
                    f"{b.text[:100]}{'…' if len(b.text) > 100 else ''}."
                )
                if not _declined_for(f"{p.name} {b.text} {b.rewritten_from}"):
                    out.append(Suggestion(type="missing_metric", detail=detail))
            if "vague_scope" in (b.gaps or []):
                out.append(
                    Suggestion(
                        type="content_gap",
                        detail=(
                            f"[{p.name}] Scope still vague after rewrite: "
                            f"{b.text[:100]}{'…' if len(b.text) > 100 else ''}."
                        ),
                    )
                )
    return out


def build_missing_skill_suggestions(
    prevalence: dict[str, float],
    owned: set[str],
    *,
    bucket: str = "",
    thin: bool = False,
    core_threshold: float = HIGH_PREVALENCE,
    common_threshold: float = COMMON_PREVALENCE,
) -> list[Suggestion]:
    out: list[Suggestion] = []
    disclosure = (
        f" (bucket `{bucket}` via SWE-family fallback — thin primary data)"
        if thin and bucket
        else (f" (bucket `{bucket}`)" if bucket else "")
    )
    for skill, prev in sorted(prevalence.items(), key=lambda x: -x[1]):
        if prev <= common_threshold:
            continue
        if skill_is_owned(skill, owned):
            continue
        tier = "core" if prev > core_threshold else "common"
        out.append(
            Suggestion(
                type="missing_skill",
                detail=(
                    f"[{tier} gap{disclosure}] {skill} appears on {prev:.0%} of similar "
                    f"resumes in the community corpus. Add it only if you actually know "
                    f"it — do not claim it for breadth or 'industry standards'."
                ),
            )
        )
    return out


def enforce_g1(
    annotated: AnnotatedGenerationResult,
    intake: Intake,
    prevalence: dict[str, float],
    *,
    bucket: str = "",
    thin: bool = False,
    declined_needles: list[str] | None = None,
    qa_store=None,
) -> GenerationResult:
    allow = allowed_skills(intake)
    cleaned_skills: dict[str, list[str]] = {}
    removed: list[str] = []
    for cat, items in (annotated.resume.skills or {}).items():
        kept: list[str] = []
        for item in items:
            item_tokens = expand_skill_tokens([item])
            if item_tokens & allow or skill_is_owned(item, allow):
                kept.append(item)
            else:
                removed.append(item)
        if kept:
            cleaned_skills[cat] = kept

    annotated.resume.skills = cleaned_skills

    # Ground experience/project technologies lines to intake + related QA (G1).
    # If the LLM left technologies empty, infer from attested intake prose.
    vocab = technology_vocab_from_intake(intake)
    for e in annotated.resume.experience:
        desc_blob = entry_attested_blob(
            intake, company=e.company, qa_store=qa_store
        )
        bullet_text = " ".join(b.text for b in (e.bullets or []))
        e.technologies = resolve_entry_technologies(
            getattr(e, "technologies", "") or "",
            f"{desc_blob}\n{bullet_text}",
            vocab,
            infer_from=desc_blob,
        )
    for p in annotated.resume.projects:
        desc_blob = entry_attested_blob(
            intake, project_name=p.name, qa_store=qa_store
        )
        bullet_text = " ".join(b.text for b in (p.bullets or []))
        # Prefer intake project.technologies when LLM blanks it
        llm_or_intake = (p.technologies or "").strip()
        if not llm_or_intake:
            for ip in intake.projects:
                if ip.name == p.name and ip.technologies:
                    llm_or_intake = ip.technologies
                    break
        p.technologies = resolve_entry_technologies(
            llm_or_intake,
            f"{desc_blob}\n{bullet_text}",
            vocab,
            infer_from=desc_blob,
        )

    resume = annotated_to_resume(annotated.resume)

    owned = owned_skills_from_intake(intake) | owned_skills_from_resume(resume)

    # Bullet-level metric/scope weaknesses are surfaced by the Phase 5 critic
    # (grounded, and deduped with revision) — not as Phase 4 suggestions. Keep
    # only non-bullet-gap suggestions the LLM emitted, then add skill gaps.
    kept_suggestions = [
        s
        for s in annotated.suggestions
        if s.type not in {"missing_skill", "missing_metric", "content_gap"}
    ]
    suggestions = list(kept_suggestions)
    suggestions.extend(
        build_missing_skill_suggestions(
            prevalence, owned, bucket=bucket, thin=thin
        )
    )

    for skill in removed:
        suggestions.append(
            Suggestion(
                type="missing_skill",
                detail=(
                    f"Removed fabricated skill {skill!r} from the resume (G1). "
                    f"Add it to intake only if you actually have it."
                ),
            )
        )

    seen: set[tuple[str, str]] = set()
    deduped: list[Suggestion] = []
    for s in suggestions:
        key = (s.type, s.detail)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(s)

    return GenerationResult(resume=resume, suggestions=deduped)


def _call_generator(
    intake: Intake,
    *,
    trim_instruction: str | None,
    fluff_instruction: str | None,
    rulebook: dict[str, Any],
    norms: dict[str, Any],
    model: str,
    phase: str,
    qa_store=None,
    expand_instruction: str | None = None,
) -> AnnotatedGenerationResult:
    norms_entry, bucket, _ = norms_entry_for_intake(norms, intake)
    prompt = build_generation_prompt(
        intake,
        rulebook=rulebook,
        norms=norms,
        trim_instruction=trim_instruction,
        fluff_instruction=fluff_instruction,
        expand_instruction=expand_instruction,
        qa_store=qa_store,
    )
    banned = sorted(banned_phrase_set())[:40]
    return complete_json(
        prompt=prompt,
        model=model,
        phase=phase,
        schema=AnnotatedGenerationResult,
        system=generator_system(
            intake,
            banned_phrases=banned,
            bullet_targets=format_bullet_targets(norms_entry, bucket=bucket),
        ),
        max_tokens=8192,
    )


def generate_resume(
    intake: Intake,
    *,
    trim_instruction: str | None = None,
    expand_instruction: str | None = None,
    rulebook_path: Path = DEFAULT_RULEBOOK,
    norms_path: Path = DEFAULT_NORMS,
    model: str = MODEL_SYNTHESIS,
    phase: str = "phase4",
    fluff_retry: bool = True,
    qa_store=None,
) -> GenerationResult:
    from src.generation.qa_store import declined_relates_to

    rulebook = load_rulebook(rulebook_path)
    norms = load_norms(norms_path)
    prevalence, bucket, thin = skill_prevalence_for_intake(norms, intake)
    declined = declined_relates_to(qa_store) if qa_store is not None else []

    annotated = _call_generator(
        intake,
        trim_instruction=trim_instruction,
        fluff_instruction=None,
        expand_instruction=expand_instruction,
        rulebook=rulebook,
        norms=norms,
        model=model,
        phase=phase,
        qa_store=qa_store,
    )
    result = enforce_g1(
        annotated,
        intake,
        prevalence,
        bucket=bucket,
        thin=thin,
        declined_needles=declined,
        qa_store=qa_store,
    )

    if fluff_retry:
        violations = lint_resume_fluff(result.resume)
        if violations:
            annotated = _call_generator(
                intake,
                trim_instruction=trim_instruction,
                fluff_instruction=fluff_retry_instruction(violations),
                expand_instruction=expand_instruction,
                rulebook=rulebook,
                norms=norms,
                model=model,
                phase=f"{phase}-fluff-retry",
                qa_store=qa_store,
            )
            result = enforce_g1(
                annotated,
                intake,
                prevalence,
                bucket=bucket,
                thin=thin,
                declined_needles=declined,
                qa_store=qa_store,
            )

    return result
