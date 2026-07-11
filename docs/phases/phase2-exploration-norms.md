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

## Status (2026-07-11)
Re-run after pre-Phase-3 data-quality fixes on the **118-thread** clean corpus
(120 raw, 98.3% survival with Tesseract OCR):

- **Bullets-per-entry:** parser now recognizes `●` and splits entries on date-line
  boundaries (no longer lumps whole sections). Gate: median in 2–4.
- **Critique taxonomy:** OP self-replies filtered via `dataset.json` authors;
  expanded categories + `gpt-4o-mini` batched labeling. Gate: `other` < 15% of
  real critiques (excl. `not_a_critique`).
- **Profile taxonomy:** Waterloo `1A`/`2B` → `year_1`/`year_2`; `intern` is no
  longer a year label; frontend/backend/fullstack fold into `swe` at the norms
  bucket layer.
- Re-run: `python -m src.knowledge.norms` then
  `python -m src.knowledge.exploration --llm-labels --yes`.

## Acceptance Criteria
- [x] `norms.json` exists with `swe_intern` norms (largest role bucket) including skill prevalence.
- [x] Skill normalization map covers the top 50 raw skill spellings observed (90% coverage on current top-50).
- [x] `FINDINGS.md` written with ≥10 quantified findings.
- [x] Pre-Phase-3 gates: bullets median 2–4, critique other <15%, survival ≥85%.

## Human Sign-Off Gate
None formally required, but share `FINDINGS.md` with Erfan as a natural checkpoint before Phase 3 (norms feed directly into rulebook/generation).

## Do Not Proceed
Do not start Phase 3 until `norms.json` has valid `swe_intern` data and `FINDINGS.md` is complete.
