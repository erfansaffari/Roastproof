"""
Phase 2 — corpus exploration charts + optional critique-category labeling.

Produces figures under notebooks/figs/ and a summary dict used by FINDINGS.md.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from src.knowledge.norms import (
    extract_skills,
    infer_year_label,
    load_threads,
    role_bucket,
    section_order,
    bullets_per_entry,
)
from src.schemas import ThreadRecord

# Categories used for critique labeling (feeds Phase 3 Rule.category vocabulary).
CRITIQUE_CATEGORIES = [
    "bullet_quality",
    "metrics",
    "skills",
    "section_order",
    "formatting",
    "ats_formatting",
    "education",
    "experience",
    "projects",
    "project_selection",
    "wording",
    "length",
    "contact",
    "links_portfolio",
    "tailoring",
    "redundancy_filler",
    "positive_feedback",
    "not_a_critique",
    "other",
]

# Categories excluded when computing the "other" rate gate.
NON_CRITIQUE_CATEGORIES = frozenset({"not_a_critique"})

SECTION_HINTS = {
    "education": re.compile(r"\b(education|gpa|coursework|degree|university|school)\b", re.I),
    "experience": re.compile(r"\b(experience|intern|work|job|company|bullet)\b", re.I),
    "projects": re.compile(r"\b(project)\b", re.I),
    "skills": re.compile(r"\b(skill|tech stack|languages?|framework)\b", re.I),
    "contact": re.compile(r"\b(email|phone|linkedin|github|header|name)\b", re.I),
    "formatting": re.compile(r"\b(format|layout|font|spacing|column|margin|one.?page)\b", re.I),
}

LLM_LABEL_SYSTEM = (
    "You label Discord resume critiques for a community corpus. "
    "Respond with ONLY a JSON array. Each element must have keys: "
    "idx (int), section_targeted, category.\n"
    "section_targeted: one of education|experience|projects|skills|contact|formatting|general. "
    "If the critique mentions a specific bullet, company, project, or section heading, "
    "assign that section; use 'general' only for whole-resume comments.\n"
    f"category: one of {', '.join(CRITIQUE_CATEGORIES)}.\n"
    "Use not_a_critique for thank-yous, questions from the poster, off-topic chat, "
    "or messages that are not feedback on the resume."
)


def load_op_authors(dataset_path: Path = Path("data/raw/dataset.json")) -> dict[str, str]:
    """Map thread_id (resume_message_id) → original poster author."""
    if not dataset_path.exists():
        return {}
    data = json.loads(dataset_path.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for row in data:
        tid = str(row.get("resume_message_id", ""))
        author = row.get("author") or ""
        if tid and author:
            out[tid] = author
    return out


def is_op_reply(thread_id: str, critique_author: str, op_authors: dict[str, str]) -> bool:
    op = op_authors.get(str(thread_id))
    if not op or not critique_author:
        return False
    return critique_author.strip().lower() == op.strip().lower()


def heuristic_section_targeted(content: str) -> str:
    for section, pattern in SECTION_HINTS.items():
        if pattern.search(content):
            return section
    return "general"


def heuristic_critique_category(content: str) -> str:
    c = content.lower().strip()
    if len(c) < 8 or re.fullmatch(r"(thanks?|ty|thx|lol|lmao|ok|okay|nice|cool)[.!]*", c):
        return "not_a_critique"
    if re.search(r"\b(looks? (good|great|solid|fine)|good resume|solid resume|nice resume)\b", c):
        return "positive_feedback"
    if re.search(r"\b(tailor|job.?desc|jd\b|match the role|for this role|target(ed)? role)\b", c):
        return "tailoring"
    if re.search(r"\b(todo app|drop .{0,40}project|remove .{0,40}project|project selection)\b", c):
        return "project_selection"
    if re.search(r"\b(redundant|filler|fluff|buzzword|remove (this|that)|drop the)\b", c):
        return "redundancy_filler"
    if re.search(r"\b(portfolio|github\.com|personal site|link(s)? (are|is)|dead link)\b", c):
        return "links_portfolio"
    if re.search(r"\b(ats|applicant tracking|keyword|parse[rd]?|recruiter.?scan)\b", c):
        return "ats_formatting"
    if re.search(r"\b(metric|quantif|number|%|percent|impact)\b", c):
        return "metrics"
    if re.search(r"\b(bullet|action verb|vague|wordy)\b", c):
        return "bullet_quality"
    if re.search(r"\b(skill|tech|stack|language|framework)\b", c):
        return "skills"
    if re.search(r"\b(order|move|above|below|section)\b", c):
        return "section_order"
    if re.search(r"\b(format|layout|font|spacing|one.?page|column)\b", c):
        return "formatting"
    if re.search(r"\b(gpa|coursework|education|degree)\b", c):
        return "education"
    if re.search(r"\b(project)\b", c):
        return "projects"
    if re.search(r"\b(intern|experience|work)\b", c):
        return "experience"
    if re.search(r"\b(wording|grammar|typo|phrase)\b", c):
        return "wording"
    if re.search(r"\b(length|too long|cut|trim|page)\b", c):
        return "length"
    if re.search(r"\b(email|linkedin|github|header)\b", c):
        return "contact"
    return "other"


def _parse_label_batch(raw: str, n: int) -> dict[int, dict[str, str]]:
    """Parse LLM JSON array of {idx, section_targeted, category}."""
    text = raw.strip()
    fence = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```$", text)
    if fence:
        text = fence.group(1).strip()
    start = text.find("[")
    end = text.rfind("]")
    if start < 0 or end <= start:
        # single object fallback
        start = text.find("{")
        end = text.rfind("}")
        if start < 0:
            return {}
        text = "[" + text[start : end + 1] + "]"
    else:
        text = text[start : end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    out: dict[int, dict[str, str]] = {}
    if isinstance(data, dict):
        data = [data]
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("idx"))
        except (TypeError, ValueError):
            continue
        if idx < 0 or idx >= n:
            continue
        section = str(item.get("section_targeted") or "").strip().lower()
        category = str(item.get("category") or "").strip().lower()
        out[idx] = {"section_targeted": section, "category": category}
    return out


def label_critiques(
    records: list[ThreadRecord],
    use_llm: bool = False,
    op_authors: dict[str, str] | None = None,
    batch_size: int = 15,
    yes: bool = False,
) -> list[dict[str, Any]]:
    """
    Label each critique with section_targeted + category.

    OP self-replies are always tagged not_a_critique/op_reply (excluded from
    the "other" rate gate). Default labeling is heuristics; --llm-labels uses
    gpt-4o-mini in batched prompts.
    """
    if op_authors is None:
        op_authors = load_op_authors()

    # Flatten work items first so we can batch LLM calls.
    items: list[dict[str, Any]] = []
    for rec in records:
        for crit in rec.critiques:
            items.append(
                {
                    "thread_id": rec.thread_id,
                    "role_bucket": role_bucket(rec),
                    "author": crit.author,
                    "content": crit.content,
                    "agreement_signal": crit.agreement_signal,
                    "is_op_reply": is_op_reply(rec.thread_id, crit.author, op_authors),
                }
            )

    labeled: list[dict[str, Any]] = []
    pending_llm: list[int] = []  # indices into items needing LLM

    for i, item in enumerate(items):
        if item["is_op_reply"]:
            labeled.append(
                {
                    **{k: item[k] for k in (
                        "thread_id", "role_bucket", "author", "content", "agreement_signal"
                    )},
                    "section_targeted": "general",
                    "category": "not_a_critique",
                    "label_source": "op_reply",
                }
            )
            continue
        if not use_llm:
            labeled.append(
                {
                    **{k: item[k] for k in (
                        "thread_id", "role_bucket", "author", "content", "agreement_signal"
                    )},
                    "section_targeted": heuristic_section_targeted(item["content"]),
                    "category": heuristic_critique_category(item["content"]),
                    "label_source": "heuristic",
                }
            )
        else:
            labeled.append(None)  # type: ignore[arg-type]
            pending_llm.append(i)

    if use_llm and pending_llm:
        from src import llm

        n_calls = (len(pending_llm) + batch_size - 1) // batch_size
        # G3: estimate + require --yes for large runs
        est_tokens = len(pending_llm) * 80  # rough
        print(
            f"LLM critique labeling: {len(pending_llm)} critiques in ~{n_calls} batched "
            f"calls (~{est_tokens} tokens est.)."
        )
        if n_calls > 100 and not yes:
            raise SystemExit("Refusing >100 API calls without --yes (G3).")

        for batch_start in range(0, len(pending_llm), batch_size):
            batch_idxs = pending_llm[batch_start : batch_start + batch_size]
            lines = []
            for j, item_i in enumerate(batch_idxs):
                content = items[item_i]["content"].replace("\n", " ").strip()
                if len(content) > 500:
                    content = content[:500] + "…"
                lines.append(f"[{j}] {content}")
            prompt = "Label these critiques:\n" + "\n".join(lines)
            try:
                raw = llm.complete(
                    prompt=prompt,
                    model=llm.MODEL_BULK,
                    phase="phase2-critique-label",
                    max_tokens=80 * len(batch_idxs) + 40,
                    system=LLM_LABEL_SYSTEM,
                )
                parsed = _parse_label_batch(raw, len(batch_idxs))
            except Exception as exc:
                print(f"  batch {batch_start}: LLM failed ({exc}); using heuristics")
                parsed = {}

            for j, item_i in enumerate(batch_idxs):
                item = items[item_i]
                got = parsed.get(j, {})
                section = got.get("section_targeted") or heuristic_section_targeted(item["content"])
                category = got.get("category") or heuristic_critique_category(item["content"])
                if section not in {
                    "education", "experience", "projects", "skills",
                    "contact", "formatting", "general",
                }:
                    section = heuristic_section_targeted(item["content"])
                if category not in CRITIQUE_CATEGORIES:
                    category = heuristic_critique_category(item["content"])
                labeled[item_i] = {
                    **{k: item[k] for k in (
                        "thread_id", "role_bucket", "author", "content", "agreement_signal"
                    )},
                    "section_targeted": section,
                    "category": category,
                    "label_source": "llm" if j in parsed else "heuristic_fallback",
                }

    # Drop any accidental Nones
    return [row for row in labeled if row is not None]


def other_rate(labeled: list[dict[str, Any]]) -> float:
    """Fraction of real critiques labeled 'other' (excludes not_a_critique)."""
    real = [x for x in labeled if x.get("category") not in NON_CRITIQUE_CATEGORIES]
    if not real:
        return 0.0
    return sum(1 for x in real if x.get("category") == "other") / len(real)


def threads_to_dataframe(records: list[ThreadRecord]) -> pd.DataFrame:
    rows = []
    for rec in records:
        bpe = bullets_per_entry(rec)
        rows.append(
            {
                "thread_id": rec.thread_id,
                "target_role": rec.target_role.value
                if hasattr(rec.target_role, "value")
                else str(rec.target_role),
                "role_bucket": role_bucket(rec),
                "year_label": infer_year_label(rec),
                "n_critiques": len(rec.critiques),
                "applicant_profile": rec.applicant_profile,
                "context_message": rec.context_message,
                "resume_chars": len(rec.resume_text),
                "n_skills": len(extract_skills(rec)),
                "section_order": " > ".join(section_order(rec)),
                "median_bullets": (
                    sorted(bpe)[len(bpe) // 2] if bpe else 0
                ),
            }
        )
    return pd.DataFrame(rows)


def _save(fig: plt.Figure, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def generate_figures(
    df: pd.DataFrame,
    labeled: list[dict[str, Any]],
    records: list[ThreadRecord],
    figs_dir: Path,
) -> dict[str, Any]:
    """Create all Phase 2 charts; return a summary dict for FINDINGS.md."""
    figs_dir.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {"n_threads": len(df), "figures": []}

    # 1. Role distribution
    fig, ax = plt.subplots(figsize=(8, 4))
    counts = df["role_bucket"].value_counts()
    counts.plot(kind="bar", ax=ax, color="#2c6eaf")
    ax.set_title("Role bucket distribution")
    ax.set_xlabel("Role bucket")
    ax.set_ylabel("Threads")
    path = figs_dir / "01_role_distribution.png"
    _save(fig, path)
    summary["figures"].append(str(path))
    summary["role_distribution"] = counts.to_dict()

    # 2. Year distribution
    fig, ax = plt.subplots(figsize=(8, 4))
    year_counts = df["year_label"].fillna("unknown").value_counts()
    year_counts.plot(kind="bar", ax=ax, color="#3d8b6e")
    ax.set_title("Applicant year / seniority label")
    ax.set_xlabel("Label")
    ax.set_ylabel("Threads")
    path = figs_dir / "02_year_distribution.png"
    _save(fig, path)
    summary["figures"].append(str(path))
    summary["year_distribution"] = year_counts.to_dict()

    # 3. Critiques per thread
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.hist(df["n_critiques"], bins=range(0, int(df["n_critiques"].max()) + 2), color="#c47a2c", edgecolor="white")
    ax.set_title("Critiques per thread")
    ax.set_xlabel("# critiques")
    ax.set_ylabel("Threads")
    path = figs_dir / "03_critiques_per_thread.png"
    _save(fig, path)
    summary["figures"].append(str(path))
    summary["critiques_per_thread"] = {
        "mean": float(df["n_critiques"].mean()) if len(df) else 0,
        "median": float(df["n_critiques"].median()) if len(df) else 0,
        "max": int(df["n_critiques"].max()) if len(df) else 0,
    }

    # 4. Critique category frequencies (exclude not_a_critique from chart title note)
    cat_counts = Counter(x["category"] for x in labeled)
    real_labeled = [x for x in labeled if x.get("category") not in NON_CRITIQUE_CATEGORIES]
    real_cat_counts = Counter(x["category"] for x in real_labeled)
    fig, ax = plt.subplots(figsize=(10, 4))
    cats = [c for c, _ in real_cat_counts.most_common()]
    vals = [real_cat_counts[c] for c in cats]
    ax.bar(cats, vals, color="#6b4c9a")
    ax.set_title("Critique category frequencies (excl. not_a_critique)")
    ax.tick_params(axis="x", rotation=55)
    ax.set_ylabel("Count")
    path = figs_dir / "04_critique_categories.png"
    _save(fig, path)
    summary["figures"].append(str(path))
    summary["critique_categories"] = dict(cat_counts.most_common())
    summary["critique_categories_real"] = dict(real_cat_counts.most_common())
    summary["other_rate"] = round(other_rate(labeled), 4)
    summary["n_not_a_critique"] = cat_counts.get("not_a_critique", 0)
    summary["n_real_critiques"] = len(real_labeled)

    section_counts = Counter(x["section_targeted"] for x in labeled)
    summary["section_targeted"] = dict(section_counts.most_common())

    # 5. Top-30 skills overall + per largest role
    skill_by_role: dict[str, Counter[str]] = defaultdict(Counter)
    overall: Counter[str] = Counter()
    for rec in records:
        bucket = role_bucket(rec)
        for skill in extract_skills(rec):
            skill_by_role[bucket][skill] += 1
            overall[skill] += 1

    top30 = overall.most_common(30)
    fig, ax = plt.subplots(figsize=(10, 5))
    if top30:
        ax.barh([s for s, _ in reversed(top30)], [c for _, c in reversed(top30)], color="#2c6eaf")
    ax.set_title("Top-30 skills (corpus-wide)")
    ax.set_xlabel("Resume count")
    path = figs_dir / "05_top30_skills.png"
    _save(fig, path)
    summary["figures"].append(str(path))
    summary["top30_skills"] = dict(top30)

    largest = max(skill_by_role.items(), key=lambda kv: sum(kv[1].values()), default=(None, Counter()))
    if largest[0]:
        top_role_skills = largest[1].most_common(30)
        fig, ax = plt.subplots(figsize=(10, 5))
        ax.barh(
            [s for s, _ in reversed(top_role_skills)],
            [c for _, c in reversed(top_role_skills)],
            color="#3d8b6e",
        )
        ax.set_title(f"Top skills — {largest[0]}")
        ax.set_xlabel("Resume count")
        path = figs_dir / f"05b_top_skills_{largest[0]}.png"
        _save(fig, path)
        summary["figures"].append(str(path))
        summary["top_skills_largest_role"] = {"role": largest[0], "skills": dict(top_role_skills)}

    # 6. Section-order patterns
    order_counts = Counter(df["section_order"].fillna(""))
    top_orders = [(o, c) for o, c in order_counts.most_common(8) if o]
    fig, ax = plt.subplots(figsize=(10, 4))
    if top_orders:
        labels = [o if len(o) < 40 else o[:37] + "…" for o, _ in top_orders]
        ax.barh(list(reversed(labels)), [c for _, c in reversed(top_orders)], color="#c47a2c")
    ax.set_title("Most common section orders")
    ax.set_xlabel("Threads")
    path = figs_dir / "06_section_order.png"
    _save(fig, path)
    summary["figures"].append(str(path))
    summary["section_orders"] = dict(top_orders)

    # 7. Bullets per entry
    all_bullets: list[int] = []
    for rec in records:
        all_bullets.extend(bullets_per_entry(rec))
    fig, ax = plt.subplots(figsize=(8, 4))
    if all_bullets:
        ax.hist(all_bullets, bins=range(1, max(all_bullets) + 2), color="#6b4c9a", edgecolor="white")
    ax.set_title("Bullets per experience/project entry")
    ax.set_xlabel("Bullets")
    ax.set_ylabel("Entries")
    path = figs_dir / "07_bullets_per_entry.png"
    _save(fig, path)
    summary["figures"].append(str(path))
    summary["bullets_per_entry"] = {
        "n_entries": len(all_bullets),
        "mean": round(sum(all_bullets) / len(all_bullets), 2) if all_bullets else 0,
        "median": sorted(all_bullets)[len(all_bullets) // 2] if all_bullets else 0,
    }

    return summary


def write_findings(summary: dict[str, Any], norms: dict[str, Any], path: Path) -> None:
    """Write ≥10 quantified findings for the portfolio artifact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    roles = norms.get("roles", {})
    buckets = norms.get("meta", {}).get("buckets", {})
    largest_role = max(buckets, key=buckets.get) if buckets else None
    largest = roles.get(largest_role or "", {})
    skills = list((largest.get("skill_prevalence") or {}).items())[:10]
    cats = list((summary.get("critique_categories_real") or summary.get("critique_categories") or {}).items())[:6]
    years = summary.get("year_distribution") or {}
    crit = summary.get("critiques_per_thread") or {}
    orders = list((summary.get("section_orders") or {}).items())[:3]
    bullets = summary.get("bullets_per_entry") or {}
    sections = list((summary.get("section_targeted") or {}).items())[:5]
    coverage = norms.get("skill_normalization_coverage") or {}

    lines = [
        "# Phase 2 Findings",
        "",
        f"_Corpus: **{summary.get('n_threads', 0)}** structured threads "
        f"(from `data/structured/threads.jsonl`). "
        f"Norms min-n threshold is {norms.get('meta', {}).get('min_n', 30)}; "
        f"buckets below that are flagged `insufficient_data` but still reported for development._",
        "",
        "## Top insights",
        "",
        f"1. **Largest role bucket is `{largest_role}`** with "
        f"**{buckets.get(largest_role, 0)}** threads "
        f"({(100 * buckets.get(largest_role, 0) / max(summary.get('n_threads', 1), 1)):.0f}% of the corpus).",
        f"2. **Role mix:** "
        + (", ".join(f"`{k}`={v}" for k, v in list(buckets.items())[:6]) or "n/a")
        + ".",
        f"3. **Year/seniority labels:** "
        + (", ".join(f"{k}={v}" for k, v in list(years.items())[:6]) or "n/a")
        + ".",
        f"4. **Critique volume:** mean **{crit.get('mean', 0):.1f}**, median **{crit.get('median', 0):.0f}**, "
        f"max **{crit.get('max', 0)}** critiques per thread.",
        f"5. **Top critique categories (real critiques):** "
        + (", ".join(f"{k} ({v})" for k, v in cats) or "n/a")
        + f". **other rate={summary.get('other_rate', 0):.0%}** "
        f"(gate: <15%; excluded {summary.get('n_not_a_critique', 0)} not_a_critique).",
        f"6. **Sections most often targeted by critiques:** "
        + (", ".join(f"{k} ({v})" for k, v in sections) or "n/a")
        + ".",
        f"7. **`{largest_role}` skill prevalence (top):** "
        + (
            ", ".join(f"{s}={p:.0%}" for s, p in skills[:8])
            if skills
            else "n/a — no skills parsed yet"
        )
        + ".",
        f"8. **Most common section order(s):** "
        + (
            "; ".join(f"`{o}` (n={c})" for o, c in orders)
            if orders
            else "n/a"
        )
        + ".",
        f"9. **Bullets per entry:** median **{bullets.get('median', 0)}**, "
        f"mean **{bullets.get('mean', 0)}** across **{bullets.get('n_entries', 0)}** entries "
        f"(one-page heuristic budget is ≤4 experience / ≤3 project bullets).",
        f"10. **Page convention for `{largest_role}`:** "
        f"`{largest.get('page_convention', 'n/a')}` "
        f"(median bullets/entry={largest.get('median_bullets_per_entry', 'n/a')}).",
        f"11. **Skill normalization coverage:** "
        f"{coverage.get('covered', 0)}/{coverage.get('observed_top', 0)} of top observed "
        f"raw spellings map cleanly "
        f"(rate={coverage.get('coverage_rate', 0):.0%}).",
        f"12. **Data sufficiency:** "
        + (
            f"`{largest_role}` is marked **insufficient_data** (n={largest.get('n', 0)} < "
            f"{norms.get('meta', {}).get('min_n', 30)}). Grow the corpus toward ~1,000 threads "
            f"before treating these norms as production priors."
            if largest.get("insufficient_data")
            else f"`{largest_role}` meets the n≥{norms.get('meta', {}).get('min_n', 30)} bar."
        ),
        "",
        "## Figures",
        "",
    ]
    for fig in summary.get("figures", []):
        rel = Path(fig).as_posix()
        lines.append(f"- `{rel}`")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def run(
    threads_path: Path,
    figs_dir: Path,
    findings_path: Path,
    summary_path: Path,
    norms_path: Path | None = None,
    use_llm_labels: bool = False,
    yes: bool = False,
) -> dict[str, Any]:
    records = load_threads(threads_path)
    df = threads_to_dataframe(records)
    labeled = label_critiques(records, use_llm=use_llm_labels, yes=yes)
    summary = generate_figures(df, labeled, records, figs_dir)

    if norms_path and norms_path.exists():
        norms = json.loads(norms_path.read_text(encoding="utf-8"))
    else:
        from src.knowledge.norms import compute_norms, build_skill_normalization_coverage

        norms = compute_norms(records)
        norms["skill_normalization_coverage"] = build_skill_normalization_coverage(records)

    write_findings(summary, norms, findings_path)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    labels_path = figs_dir.parent / "critique_labels.jsonl"
    with open(labels_path, "w", encoding="utf-8") as f:
        for row in labeled:
            f.write(json.dumps(row) + "\n")

    print(
        f"Critique labels: {len(labeled)} total, "
        f"{summary.get('n_not_a_critique', 0)} not_a_critique, "
        f"other_rate={summary.get('other_rate', 0):.1%} "
        f"(of {summary.get('n_real_critiques', 0)} real)"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 2 exploration charts + findings.")
    parser.add_argument("--threads", type=Path, default=Path("data/structured/threads.jsonl"))
    parser.add_argument("--figs-dir", type=Path, default=Path("notebooks/figs"))
    parser.add_argument("--findings", type=Path, default=Path("notebooks/FINDINGS.md"))
    parser.add_argument("--summary", type=Path, default=Path("notebooks/exploration_summary.json"))
    parser.add_argument("--norms", type=Path, default=Path("data/norms/norms.json"))
    parser.add_argument(
        "--llm-labels",
        action="store_true",
        help="Use gpt-4o-mini to label critique categories (costs API calls).",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Confirm runs that would make >100 API calls (G3).",
    )
    args = parser.parse_args()

    if not args.threads.exists():
        raise SystemExit(f"{args.threads} not found. Finish Phase 1 first.")

    summary = run(
        args.threads,
        args.figs_dir,
        args.findings,
        args.summary,
        norms_path=args.norms if args.norms.exists() else None,
        use_llm_labels=args.llm_labels,
        yes=args.yes,
    )
    print(f"Wrote {len(summary.get('figures', []))} figures to {args.figs_dir}")
    print(f"Wrote {args.findings}")
    print("Role distribution:", summary.get("role_distribution"))
    print(f"other_rate={summary.get('other_rate')}")


if __name__ == "__main__":
    main()