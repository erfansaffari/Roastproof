"""
Phase 2 — per-role statistical norms.

Reads data/structured/threads.jsonl and writes:
  - data/norms/norms.json
  - data/norms/norms.db  (SQLite mirror)

Role buckets (e.g. swe_intern) combine TargetRole + seniority signals from the
applicant profile / context message. Norms with n < MIN_N are still written but
flagged insufficient_data=True (PRD threshold is 30).
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median
from typing import Any, Iterable

from src.schemas import ThreadRecord

MIN_N = 30

# Canonical skill spellings. Keys are lowercased lookup forms.
SKILL_NORMALIZATION: dict[str, str] = {
    # languages
    "javascript": "JavaScript",
    "js": "JavaScript",
    "typescript": "TypeScript",
    "ts": "TypeScript",
    "python": "Python",
    "py": "Python",
    "java": "Java",
    "c++": "C++",
    "cpp": "C++",
    "c": "C",
    "c#": "C#",
    "csharp": "C#",
    "go": "Go",
    "golang": "Go",
    "rust": "Rust",
    "ruby": "Ruby",
    "php": "PHP",
    "swift": "Swift",
    "kotlin": "Kotlin",
    "scala": "Scala",
    "r": "R",
    "matlab": "MATLAB",
    "sql": "SQL",
    "html": "HTML",
    "css": "CSS",
    "html/css": "HTML/CSS",
    "bash": "Bash",
    "shell": "Shell",
    "dart": "Dart",
    # frameworks / libs
    "react": "React",
    "react.js": "React",
    "reactjs": "React",
    "next.js": "Next.js",
    "nextjs": "Next.js",
    "vue": "Vue",
    "vue.js": "Vue",
    "angular": "Angular",
    "node": "Node.js",
    "node.js": "Node.js",
    "nodejs": "Node.js",
    "express": "Express",
    "express.js": "Express",
    "django": "Django",
    "flask": "Flask",
    "fastapi": "FastAPI",
    "spring": "Spring",
    "spring boot": "Spring Boot",
    "springboot": "Spring Boot",
    "rails": "Rails",
    "dotnet": ".NET",
    ".net": ".NET",
    "pandas": "pandas",
    "numpy": "NumPy",
    "scikit-learn": "scikit-learn",
    "sklearn": "scikit-learn",
    "pytorch": "PyTorch",
    "tensorflow": "TensorFlow",
    "keras": "Keras",
    "xgboost": "XGBoost",
    "opencv": "OpenCV",
    "flutter": "Flutter",
    # infra / tools
    "git": "Git",
    "github": "GitHub",
    "gitlab": "GitLab",
    "docker": "Docker",
    "kubernetes": "Kubernetes",
    "k8s": "Kubernetes",
    "aws": "AWS",
    "gcp": "GCP",
    "google cloud": "GCP",
    "azure": "Azure",
    "linux": "Linux",
    "unix": "Unix",
    "mongodb": "MongoDB",
    "postgres": "PostgreSQL",
    "postgresql": "PostgreSQL",
    "mysql": "MySQL",
    "redis": "Redis",
    "kafka": "Kafka",
    "rabbitmq": "RabbitMQ",
    "terraform": "Terraform",
    "jenkins": "Jenkins",
    "ci/cd": "CI/CD",
    "graphql": "GraphQL",
    "rest": "REST",
    "figma": "Figma",
    "jira": "Jira",
    "spark": "Spark",
    "hadoop": "Hadoop",
    "airflow": "Airflow",
    "vercel": "Vercel",
    "firebase": "Firebase",
}

SECTION_HEADERS = [
    ("education", re.compile(r"^\s*(education|academic)\b", re.I)),
    ("experience", re.compile(r"^\s*(experience|work experience|employment|internship)\b", re.I)),
    ("projects", re.compile(r"^\s*(projects?|personal projects?)\b", re.I)),
    ("skills", re.compile(r"^\s*(technical skills|skills|technologies|tech stack)\b", re.I)),
    ("coursework", re.compile(r"^\s*(relevant coursework|coursework)\b", re.I)),
    ("activities", re.compile(r"^\s*(activities|extracurricular|leadership|involvement)\b", re.I)),
    ("awards", re.compile(r"^\s*(awards|honors|achievements)\b", re.I)),
    ("summary", re.compile(r"^\s*(summary|objective|profile)\b", re.I)),
]

# Include common PDF-extracted bullet glyphs (● is the most common miss).
BULLET_RE = re.compile(r"^\s*[•●◦▪‣○\-\*∙·–—]\s+")

# Date line used as an entry boundary (Jake's-style resumes rarely have blank lines).
DATE_LINE_RE = re.compile(
    r"(?:"
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)\.?\s+\d{4}"
    r"|\d{4}\s*[-–—]\s*(?:\d{4}|[Pp]resent|[Cc]urrent)"
    r"|(?:[Pp]resent|[Cc]urrent)"
    r")"
)

YEAR_RE = re.compile(
    r"\b(?P<label>\d[AB]|1st|2nd|3rd|4th|first|second|third|fourth)?\s*"
    r"(?P<year>year|yr)?\b|"
    r"\b(?P<code>[1-4][AB]|co-?op)\b|"
    r"\b(new\s*grad|graduating|class of\s*(?P<grad>\d{4})|expected\s+\w+\.?\s*(?P<exp>\d{4}))\b",
    re.I,
)


def normalize_skill(raw: str) -> str | None:
    """Map a raw skill token to a canonical name, or None if empty/junk."""
    token = raw.strip().strip(",;|/").strip()
    if not token or len(token) > 40:
        return None
    key = token.lower()
    if key in SKILL_NORMALIZATION:
        return SKILL_NORMALIZATION[key]
    # Title-case unknown but plausible tokens (letters/digits/#+.)
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9+#./\-]{0,38}", token):
        return SKILL_NORMALIZATION.get(key, token if token[0].isupper() else token.title())
    return None


def split_resume_sections(resume_text: str) -> dict[str, str]:
    """Heuristic section splitter for Jake's-style plain-text resumes."""
    lines = resume_text.splitlines()
    sections: dict[str, list[str]] = defaultdict(list)
    current = "header"
    for line in lines:
        matched = None
        for name, pattern in SECTION_HEADERS:
            if pattern.match(line.strip()):
                matched = name
                break
        if matched:
            current = matched
            continue
        sections[current].append(line)
    return {k: "\n".join(v).strip() for k, v in sections.items() if "\n".join(v).strip()}


def parse_skills_from_text(skills_text: str) -> list[str]:
    """Parse a skills section into normalized unique skill names."""
    if not skills_text:
        return []
    # Drop category labels like "Languages:" then split on commas / pipes / slashes.
    cleaned = re.sub(r"(?i)\b(languages?|frameworks?|libraries?|tools?|technologies?|developer tools?)\s*:", ",", skills_text)
    parts = re.split(r"[,|/•\n;]+", cleaned)
    seen: set[str] = set()
    out: list[str] = []
    for part in parts:
        skill = normalize_skill(part)
        if skill and skill not in seen:
            seen.add(skill)
            out.append(skill)
    return out


def extract_skills(record: ThreadRecord) -> list[str]:
    sections = record.resume_sections or {}
    skills_blob = sections.get("skills") or ""
    if not skills_blob:
        skills_blob = split_resume_sections(record.resume_text).get("skills", "")
    if not skills_blob:
        # Fallback: scan whole resume for known skill tokens.
        found: list[str] = []
        seen: set[str] = set()
        lower = record.resume_text.lower()
        for raw, canon in SKILL_NORMALIZATION.items():
            # word-ish boundary for short tokens
            if re.search(rf"(?<![A-Za-z0-9+#]){re.escape(raw)}(?![A-Za-z0-9+#])", lower):
                if canon not in seen:
                    seen.add(canon)
                    found.append(canon)
        return found
    return parse_skills_from_text(skills_blob)


def section_order(record: ThreadRecord) -> list[str]:
    sections = record.resume_sections or {}
    if sections:
        # Preserve insertion order if already structured.
        return [k for k in sections.keys() if k != "header"]
    split = split_resume_sections(record.resume_text)
    order: list[str] = []
    # Re-walk text to get true order
    for line in record.resume_text.splitlines():
        for name, pattern in SECTION_HEADERS:
            if pattern.match(line.strip()) and name not in order:
                order.append(name)
                break
    return order or [k for k in split.keys() if k != "header"]


def _split_entries(block: str) -> list[str]:
    """
    Split an experience/projects section into per-job/project entries.

    Prefer date-line boundaries (common in extracted text with no blank lines).
    Fall back to blank-line splits, then consecutive-bullet-run grouping.
    """
    if not block.strip():
        return []

    lines = block.splitlines()
    date_idxs = [i for i, line in enumerate(lines) if DATE_LINE_RE.search(line)]

    if len(date_idxs) >= 2:
        # An entry starts a few lines before its date (company/title), or at the
        # date line itself. Use the midpoint between consecutive dates as a
        # conservative boundary, clamped to the previous date.
        starts = [0]
        for prev, curr in zip(date_idxs, date_idxs[1:]):
            # Start of next entry: first non-bullet line after previous date's
            # bullet block, looking backward from curr for a title/company line.
            start = curr
            for j in range(curr - 1, prev, -1):
                if BULLET_RE.match(lines[j]):
                    break
                start = j
            starts.append(start)
        entries: list[str] = []
        for i, start in enumerate(starts):
            end = starts[i + 1] if i + 1 < len(starts) else len(lines)
            chunk = "\n".join(lines[start:end]).strip()
            if chunk:
                entries.append(chunk)
        return entries

    blank_split = [e.strip() for e in re.split(r"\n\s*\n", block) if e.strip()]
    if len(blank_split) > 1:
        return blank_split

    # Fallback: group consecutive bullet runs; a non-bullet line after bullets
    # starts a new entry.
    entries = []
    current: list[str] = []
    saw_bullet = False
    for line in lines:
        is_bullet = bool(BULLET_RE.match(line))
        if saw_bullet and not is_bullet and line.strip():
            entries.append("\n".join(current).strip())
            current = [line]
            saw_bullet = False
        else:
            current.append(line)
            if is_bullet:
                saw_bullet = True
    if current:
        entries.append("\n".join(current).strip())
    return [e for e in entries if e]


def bullets_per_entry(record: ThreadRecord) -> list[int]:
    """Count bullets under each experience/project entry (not whole section)."""
    split = split_resume_sections(record.resume_text)
    counts: list[int] = []
    for key in ("experience", "projects"):
        block = split.get(key, "")
        if not block:
            continue
        for entry in _split_entries(block):
            n = sum(1 for line in entry.splitlines() if BULLET_RE.match(line))
            if n:
                counts.append(n)
    return counts


def has_internships(record: ThreadRecord) -> bool:
    """True if profile/context/resume text indicates internship experience."""
    blob = f"{record.applicant_profile or ''} {record.context_message or ''} {record.resume_text[:800]}"
    return bool(re.search(r"\bintern(ship)?s?\b", blob, re.I))


def infer_year_label(record: ThreadRecord) -> str | None:
    """
    Normalize seniority to school-year buckets.

    Waterloo term codes map to year_1..year_4. "intern" is NOT a year — use
    has_internships() for that signal.
    """
    blob = f"{record.applicant_profile or ''} {record.context_message or ''}"

    m = re.search(r"\b([1-4])[AB]\b", blob, re.I)
    if m:
        return f"year_{m.group(1)}"

    # Hyphenated forms are common: "Third-year student"
    if re.search(r"\b(1st|first)[\s-]*year\b", blob, re.I):
        return "year_1"
    if re.search(r"\b(2nd|second)[\s-]*year\b", blob, re.I):
        return "year_2"
    if re.search(r"\b(3rd|third)[\s-]*year\b", blob, re.I):
        return "year_3"
    if re.search(r"\b(4th|fourth)[\s-]*year\b", blob, re.I):
        return "year_4"

    if re.search(r"\b(master'?s|grad(?:uate)?\s+student|msc|meng)\b", blob, re.I):
        return "grad"
    if re.search(
        r"\bnew\s*grad|graduating|recent\s+grad(?:uate)?|"
        r"graduate\s+in\s+\w*\.?\s*\d{4}|convocation|entry[- ]level\b",
        blob,
        re.I,
    ):
        return "new_grad"
    if re.search(r"\b(senior|staff|principal)\b", blob, re.I):
        return "senior"
    return None


def role_bucket(record: ThreadRecord) -> str:
    """
    Map a ThreadRecord to a norms bucket key.

    Bucket mapping (norms layer — TargetRole enum in schemas.py is unchanged):
      - swe_intern / swe_new_grad / swe  (frontend/backend/fullstack fold into swe)
      - data_intern / data
      - ml
    Small specialty roles never hit n≥30 alone, so they fold into swe.
    """
    role = record.target_role.value if hasattr(record.target_role, "value") else str(record.target_role)
    role_l = role.lower()
    blob = f"{record.applicant_profile or ''} {record.context_message or ''}".lower()

    year = infer_year_label(record)
    is_intern = has_internships(record) or (year is not None and year.startswith("year_"))
    is_new_grad = year == "new_grad" or bool(
        re.search(r"\bnew\s*grad|graduating|convocation|entry[- ]level\b", blob)
    )

    if "data" in role_l and "scientist" in role_l:
        return "data_intern" if is_intern and not is_new_grad else "data"
    if "machine learning" in role_l or role_l.startswith("ml"):
        return "ml"

    # SWE family (incl. frontend / backend / fullstack / other engineer)
    if (
        "software" in role_l
        or "engineer" in role_l
        or "full stack" in role_l
        or "frontend" in role_l
        or "backend" in role_l
        or role_l in {"other"}
    ):
        if is_intern and not is_new_grad:
            return "swe_intern"
        if is_new_grad:
            return "swe_new_grad"
        return "swe"

    if "data" in role_l:
        return "data_intern" if is_intern and not is_new_grad else "data"
    return re.sub(r"[^a-z0-9]+", "_", role_l).strip("_") or "other"


def page_convention(record: ThreadRecord) -> str:
    """Heuristic one-page vs multi-page signal from text length / section density."""
    chars = len(record.resume_text)
    bullets = sum(1 for line in record.resume_text.splitlines() if BULLET_RE.match(line))
    if chars > 5500 or bullets > 22:
        return "multi_page_risk"
    return "one_page"


def load_threads(path: Path) -> list[ThreadRecord]:
    records: list[ThreadRecord] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            records.append(ThreadRecord.model_validate_json(line))
    return records


def compute_norms(records: Iterable[ThreadRecord], min_n: int = MIN_N) -> dict[str, Any]:
    by_bucket: dict[str, list[ThreadRecord]] = defaultdict(list)
    for rec in records:
        by_bucket[role_bucket(rec)].append(rec)

    norms: dict[str, Any] = {
        "meta": {
            "min_n": min_n,
            "total_threads": sum(len(v) for v in by_bucket.values()),
            "buckets": {k: len(v) for k, v in sorted(by_bucket.items(), key=lambda x: -len(x[1]))},
        },
        "roles": {},
    }

    for bucket, group in sorted(by_bucket.items(), key=lambda x: -len(x[1])):
        n = len(group)
        skill_counts: Counter[str] = Counter()
        for rec in group:
            for skill in extract_skills(rec):
                skill_counts[skill] += 1

        skill_prevalence = {
            skill: round(count / n, 4) for skill, count in skill_counts.most_common()
        }

        order_counts: Counter[tuple[str, ...]] = Counter()
        for rec in group:
            order = tuple(section_order(rec))
            if order:
                order_counts[order] += 1
        mode_order = list(order_counts.most_common(1)[0][0]) if order_counts else []

        all_bullet_counts: list[int] = []
        for rec in group:
            all_bullet_counts.extend(bullets_per_entry(rec))
        med_bullets = float(median(all_bullet_counts)) if all_bullet_counts else 0.0

        page_counts = Counter(page_convention(rec) for rec in group)
        page_mode = page_counts.most_common(1)[0][0] if page_counts else "one_page"

        entry: dict[str, Any] = {
            "n": n,
            "insufficient_data": n < min_n,
            "skill_prevalence": skill_prevalence,
            "section_order_modes": mode_order,
            "median_bullets_per_entry": med_bullets,
            "page_convention": page_mode,
        }
        norms["roles"][bucket] = entry

    return norms


def build_skill_normalization_coverage(
    records: Iterable[ThreadRecord], top_k: int = 50
) -> dict[str, Any]:
    """Report how many of the top-K observed raw skill spellings are in the map."""
    raw_counts: Counter[str] = Counter()
    for rec in records:
        sections = split_resume_sections(rec.resume_text)
        skills_blob = (rec.resume_sections or {}).get("skills") or sections.get("skills", "")
        cleaned = re.sub(
            r"(?i)\b(languages?|frameworks?|libraries?|tools?|technologies?|developer tools?)\s*:",
            ",",
            skills_blob,
        )
        for part in re.split(r"[,|/•\n;]+", cleaned):
            token = part.strip().strip(",;|/").strip()
            if token:
                raw_counts[token] += 1

    top = raw_counts.most_common(top_k)
    covered = []
    missing = []
    for raw, count in top:
        if raw.lower() in SKILL_NORMALIZATION or normalize_skill(raw):
            covered.append({"raw": raw, "count": count, "canonical": normalize_skill(raw)})
        else:
            missing.append({"raw": raw, "count": count})
    return {
        "top_k": top_k,
        "observed_top": len(top),
        "covered": len(covered),
        "missing": missing,
        "coverage_rate": round(len(covered) / len(top), 4) if top else 1.0,
    }


def write_norms_json(norms: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(norms, indent=2), encoding="utf-8")


def write_norms_sqlite(norms: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE role_norms (
                role TEXT PRIMARY KEY,
                n INTEGER NOT NULL,
                insufficient_data INTEGER NOT NULL,
                median_bullets_per_entry REAL,
                page_convention TEXT,
                section_order_json TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE skill_prevalence (
                role TEXT NOT NULL,
                skill TEXT NOT NULL,
                prevalence REAL NOT NULL,
                PRIMARY KEY (role, skill)
            )
            """
        )
        for role, entry in norms.get("roles", {}).items():
            cur.execute(
                "INSERT INTO role_norms VALUES (?, ?, ?, ?, ?, ?)",
                (
                    role,
                    entry["n"],
                    int(entry["insufficient_data"]),
                    entry["median_bullets_per_entry"],
                    entry["page_convention"],
                    json.dumps(entry["section_order_modes"]),
                ),
            )
            for skill, prev in entry.get("skill_prevalence", {}).items():
                cur.execute(
                    "INSERT INTO skill_prevalence VALUES (?, ?, ?)",
                    (role, skill, prev),
                )
        conn.commit()
    finally:
        conn.close()


def run(
    threads_path: Path,
    out_json: Path,
    out_db: Path,
    min_n: int = MIN_N,
) -> dict[str, Any]:
    records = load_threads(threads_path)
    norms = compute_norms(records, min_n=min_n)
    norms["skill_normalization_coverage"] = build_skill_normalization_coverage(records)
    write_norms_json(norms, out_json)
    write_norms_sqlite(norms, out_db)
    return norms


def main() -> None:
    parser = argparse.ArgumentParser(description="Compute Phase 2 resume norms.")
    parser.add_argument(
        "--threads",
        type=Path,
        default=Path("data/structured/threads.jsonl"),
        help="Path to Phase 1 threads.jsonl",
    )
    parser.add_argument(
        "--out-json",
        type=Path,
        default=Path("data/norms/norms.json"),
    )
    parser.add_argument(
        "--out-db",
        type=Path,
        default=Path("data/norms/norms.db"),
    )
    parser.add_argument("--min-n", type=int, default=MIN_N)
    parser.add_argument(
        "--debug-bullets",
        type=int,
        metavar="N",
        default=0,
        help="Print per-entry bullet counts for N sample resumes (verification).",
    )
    args = parser.parse_args()

    if not args.threads.exists():
        raise SystemExit(
            f"{args.threads} not found. Finish Phase 1 (structure + filter) first."
        )

    if args.debug_bullets:
        records = load_threads(args.threads)
        for rec in records[: args.debug_bullets]:
            counts = bullets_per_entry(rec)
            print(f"\n=== {rec.thread_id} year={infer_year_label(rec)} bucket={role_bucket(rec)} ===")
            print(f"bullets_per_entry={counts} median={sorted(counts)[len(counts)//2] if counts else 0}")
            split = split_resume_sections(rec.resume_text)
            for key in ("experience", "projects"):
                block = split.get(key, "")
                if not block:
                    continue
                entries = _split_entries(block)
                print(f"  {key}: {len(entries)} entries")
                for i, entry in enumerate(entries):
                    n = sum(1 for line in entry.splitlines() if BULLET_RE.match(line))
                    first = next((ln.strip() for ln in entry.splitlines() if ln.strip()), "")
                    print(f"    entry[{i}]: {n} bullets | {first[:70]}")
        return

    norms = run(args.threads, args.out_json, args.out_db, min_n=args.min_n)
    print(f"Wrote {args.out_json} and {args.out_db}")
    print("Buckets:", norms["meta"]["buckets"])
    for role, entry in norms["roles"].items():
        flag = "INSUFFICIENT" if entry["insufficient_data"] else "OK"
        top = list(entry["skill_prevalence"].items())[:5]
        print(f"  {role}: n={entry['n']} [{flag}] top_skills={top}")
        print(f"       median_bullets_per_entry={entry['median_bullets_per_entry']}")


if __name__ == "__main__":
    main()
