#!/usr/bin/env python3
"""
Phase 3 retrieval QA — 10 canned queries for Erfan to grade.

Usage:
  python scripts/test_retrieval.py
  python scripts/test_retrieval.py --persist-dir data/chroma --top 5

Prints top-5 critiques per query. Grade relevance (≥8/10 look right) and
record results in NOTES.md before starting Phase 4.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow `python scripts/test_retrieval.py` from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.knowledge.retrieve import format_for_prompt, retrieve
from src.schemas import ApplicantProfile

CANNED_QUERIES: list[dict] = [
    {
        "name": "SWE intern — experience metrics",
        "profile": ApplicantProfile(
            target_role="Software Engineer",
            year="year_2",
            has_internships=True,
            profile_summary="2B CS looking for SWE internships",
        ),
        "section": "experience",
        "query": "weak bullets missing metrics and impact numbers",
    },
    {
        "name": "SWE intern — projects selection",
        "profile": ApplicantProfile(
            target_role="Software Engineer",
            year="year_1",
            has_internships=False,
            profile_summary="1B student first co-op",
        ),
        "section": "projects",
        "query": "drop toy projects like todo apps; keep impressive projects",
    },
    {
        "name": "SWE — formatting one page",
        "profile": ApplicantProfile(
            target_role="Software Engineer",
            year="unknown",
            has_internships=True,
            profile_summary="CS student resume too long",
        ),
        "section": "formatting",
        "query": "resume is cluttered and over one page",
    },
    {
        "name": "SWE — skills section",
        "profile": ApplicantProfile(
            target_role="Software Engineer",
            year="year_3",
            has_internships=True,
            profile_summary="3A Waterloo SWE",
        ),
        "section": "skills",
        "query": "skills list is too long / poorly organized",
    },
    {
        "name": "SWE — general whole-resume",
        "profile": ApplicantProfile(
            target_role="Software Engineer",
            year="year_2",
            has_internships=True,
            profile_summary="looking for summer co-op",
        ),
        "section": "general",
        "query": "overall resume feedback tailor to the role",
    },
    {
        "name": "Data — experience bullets",
        "profile": ApplicantProfile(
            target_role="Data Scientist",
            year="year_3",
            has_internships=True,
            profile_summary="stats major seeking data science co-op",
        ),
        "section": "experience",
        "query": "data science internship bullets need stronger quantification",
    },
    {
        "name": "SWE new grad — length",
        "profile": ApplicantProfile(
            target_role="Software Engineer",
            year="new_grad",
            has_internships=True,
            profile_summary="new grad looking for full time",
        ),
        "section": "general",
        "query": "cut fluff and redundancy; tighten wording",
    },
    {
        "name": "SWE — education GPA",
        "profile": ApplicantProfile(
            target_role="Software Engineer",
            year="year_1",
            has_internships=False,
            profile_summary="first year CS",
        ),
        "section": "education",
        "query": "should I include GPA and coursework",
    },
    {
        "name": "ML — projects",
        "profile": ApplicantProfile(
            target_role="Machine Learning Engineer",
            year="year_4",
            has_internships=True,
            profile_summary="ML focused student",
        ),
        "section": "projects",
        "query": "ML project descriptions lack results and model details",
    },
    {
        "name": "SWE — action verbs / wording",
        "profile": ApplicantProfile(
            target_role="Software Engineer",
            year="unknown",
            has_internships=True,
            profile_summary="co-op applicant",
        ),
        "section": "experience",
        "query": "vague wording and weak action verbs in bullets",
    },
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 3 retrieval QA (10 canned queries).")
    parser.add_argument("--persist-dir", type=Path, default=Path("data/chroma"))
    parser.add_argument("--top", type=int, default=5)
    args = parser.parse_args()

    if not args.persist_dir.exists():
        raise SystemExit(
            f"{args.persist_dir} not found. Run: "
            "python -m src.knowledge.vectorstore --rebuild"
        )

    print("=" * 72)
    print("Phase 3 Retrieval QA — grade each query ✓/✗ in NOTES.md")
    print("Target: ≥8/10 look relevant before Phase 4.")
    print("=" * 72)

    for i, q in enumerate(CANNED_QUERIES, 1):
        print(f"\n### Q{i}: {q['name']}")
        print(f"section={q['section']}  role={q['profile'].target_role}  "
              f"year={q['profile'].year}")
        print(f"query: {q['query']}")
        try:
            points = retrieve(
                profile=q["profile"],
                section=q["section"],
                query_text=q["query"],
                k=args.top,
                persist_dir=args.persist_dir,
            )
            print(format_for_prompt(points))
            # Terminal display only — generator path uses full text (max_chars=None).
            # Uncomment for shorter terminal dumps:
            # print(format_for_prompt(points, max_chars=400))
        except Exception as e:
            print(f"  ERROR: {e}")
        print("-" * 72)

    print(
        "\nRecord grades in NOTES.md, e.g.:\n"
        "  Phase 3 retrieval QA (YYYY-MM-DD): 8/10 relevant — notes…"
    )


if __name__ == "__main__":
    main()
