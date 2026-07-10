# Phase 2 — Data Exploration & Norms

## Objective
Understand the corpus; produce per-role statistical norms the generator will cite.

## Prerequisites
- Phase 1 complete: `data/structured/threads.jsonl` exists, validated, ≥85% survival rate.

## Tasks
1. **`notebooks/01_exploration.ipynb`**
   - Load `threads.jsonl` into pandas.
   - Produce: role distribution, year distribution, critiques-per-thread histogram, critique category frequencies (via `section_targeted` + a Haiku labeling pass mapping `issue` to the Rule categories in the schema), top-30 skills per role, section-order patterns, bullets-per-entry distribution.
   - Save each chart to `notebooks/figs/`.
2. **`norms.py`** — compute and persist to `data/norms/norms.json` + SQLite:
   - `skill_prevalence[role][skill]` = fraction of resumes listing it (skills parsed from `resume_sections.skills` with a normalization map: "Javascript"→"JavaScript", "c++"→"C++", etc.).
   - `section_order_modes[role]`, `median_bullets_per_entry[role]`, `page_convention[role]`.
   - Only emit norms where n ≥ 30 resumes for that role; otherwise mark `insufficient_data`.
3. **Findings writeup** — `notebooks/FINDINGS.md`: top 10 insights with numbers (portfolio artifact).

## Acceptance Criteria
- [ ] `norms.json` exists with `swe_intern` norms (largest role bucket) including skill prevalence.
- [ ] Skill normalization map covers the top 50 raw skill spellings observed.
- [ ] `FINDINGS.md` written with ≥10 quantified findings.

## Human Sign-Off Gate
None formally required, but share `FINDINGS.md` with Erfan as a natural checkpoint before Phase 3 (norms feed directly into rulebook/generation).

## Do Not Proceed
Do not start Phase 3 until `norms.json` has valid `swe_intern` data and `FINDINGS.md` is complete.
