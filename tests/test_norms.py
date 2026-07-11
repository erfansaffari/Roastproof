"""Unit tests for Phase 2 norms helpers."""

from src.knowledge.norms import (
    normalize_skill,
    parse_skills_from_text,
    split_resume_sections,
    role_bucket,
    compute_norms,
    extract_skills,
    bullets_per_entry,
    _split_entries,
    infer_year_label,
    has_internships,
)
from src.schemas import ThreadRecord, TargetRole, Critique


def test_normalize_skill_aliases():
    assert normalize_skill("Javascript") == "JavaScript"
    assert normalize_skill("c++") == "C++"
    assert normalize_skill("nodejs") == "Node.js"
    assert normalize_skill("  python  ") == "Python"


def test_parse_skills_from_text():
    blob = "Languages: Python, Javascript, C++\nTools: Docker, git"
    skills = parse_skills_from_text(blob)
    assert "Python" in skills
    assert "JavaScript" in skills
    assert "C++" in skills
    assert "Docker" in skills
    assert "Git" in skills


def test_split_resume_sections():
    text = """Jane Doe
Education
Waterloo BCS
Experience
SWE Intern
• Built things
Projects
Cool App
• Did stuff
Technical Skills
Python, React
"""
    sections = split_resume_sections(text)
    assert "education" in sections
    assert "experience" in sections
    assert "projects" in sections
    assert "skills" in sections
    assert "Python" in sections["skills"]


NO_BLANK_LINE_RESUME = """Jane Doe
Education
Waterloo BCS
Experience
Acme Corp
Jan 2024 - May 2024
Software Engineer Intern
● Built an API that cut latency by 40 percent under peak load
● Added caching layer for hot paths serving millions of requests
● Wrote integration tests covering the payment retry workflow
Beta Inc
May 2023 - Aug 2023
Backend Intern
● Migrated legacy jobs to a queue reducing failures by 20 percent
● Documented on-call runbooks for the payments service
Projects
Cool App | Python, React
Jan 2025 - Mar 2025
● Shipped a full-stack dashboard used by 50 beta users weekly
● Added OAuth login and role-based access control
Technical Skills
Python, React, Docker
"""


def _rec(**kwargs) -> ThreadRecord:
    base = {
        "thread_id": "t1",
        "target_role": TargetRole.SOFTWARE_ENGINEER,
        "resume_text": "Technical Skills\nPython, Docker, React\nExperience\nIntern\n• Built an API that cut latency by 40 percent under load\n",
        "context_message": "2B CS looking for SWE internships",
        "applicant_profile": "2B CS student",
        "critiques": [Critique(author="a", content="add metrics to bullets please")],
    }
    base.update(kwargs)
    return ThreadRecord(**base)


def test_bullets_per_entry_splits_on_dates_with_filled_circles():
    rec = _rec(resume_text=NO_BLANK_LINE_RESUME)
    counts = bullets_per_entry(rec)
    # 2 experience entries (3 + 2) and 1 project entry (2)
    assert counts == [3, 2, 2]
    assert sorted(counts)[len(counts) // 2] == 2


def test_split_entries_date_boundaries():
    block = """Acme
Jan 2024 - May 2024
Intern
● one
● two
● three
Beta
May 2023 - Aug 2023
Intern
● four
● five
"""
    entries = _split_entries(block)
    assert len(entries) == 2
    assert sum(1 for ln in entries[0].splitlines() if ln.strip().startswith("●")) == 3
    assert sum(1 for ln in entries[1].splitlines() if ln.strip().startswith("●")) == 2


def test_role_bucket_swe_intern():
    assert role_bucket(_rec()) == "swe_intern"


def test_role_bucket_new_grad():
    rec = _rec(
        context_message="Done with courses, looking for swe",
        applicant_profile="New grad, multiple internships",
    )
    assert role_bucket(rec) == "swe_new_grad"


def test_role_bucket_folds_fullstack_into_swe():
    rec = _rec(
        target_role=TargetRole.FULL_STACK_ENGINEER,
        context_message="looking for full stack roles",
        applicant_profile="Senior engineer, 5 YoE",
        resume_text="Technical Skills\nPython, React\nExperience\nAcme\nJan 2020 - Present\nEngineer\n• Built things that scaled to millions of users under load\n",
    )
    assert role_bucket(rec) == "swe"


def test_infer_year_maps_term_codes():
    assert infer_year_label(_rec(context_message="1B CS student", applicant_profile="")) == "year_1"
    assert infer_year_label(_rec(context_message="I'm a 2A looking for co-op", applicant_profile="")) == "year_2"
    assert infer_year_label(_rec(context_message="second year CS", applicant_profile="")) == "year_2"
    # Hyphenated + grad phrasings
    assert infer_year_label(_rec(context_message="Third-year Math student", applicant_profile="")) == "year_3"
    assert infer_year_label(_rec(context_message="Recent graduate in CS", applicant_profile="")) == "new_grad"
    assert infer_year_label(
        _rec(context_message="expected to graduate in June 2025", applicant_profile="")
    ) == "new_grad"
    # "intern" alone is NOT a year
    assert (
        infer_year_label(
            _rec(context_message="looking for internships", applicant_profile="CS student")
        )
        is None
    )
    assert has_internships(
        _rec(context_message="looking for internships", applicant_profile="CS student")
    )


def test_compute_norms_marks_insufficient():
    records = [_rec(thread_id=f"t{i}") for i in range(3)]
    norms = compute_norms(records, min_n=30)
    assert "swe_intern" in norms["roles"]
    entry = norms["roles"]["swe_intern"]
    assert entry["n"] == 3
    assert entry["insufficient_data"] is True
    assert "Python" in entry["skill_prevalence"]
    assert entry["skill_prevalence"]["Python"] == 1.0


def test_extract_skills_from_resume():
    skills = extract_skills(_rec())
    assert "Python" in skills
    assert "Docker" in skills
