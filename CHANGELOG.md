# Changelog

Session-by-session record of codebase changes, for writing git commits.
Newest entries at the top. Suggested commit messages included per entry.

---

## 2026-07-11 (b) — Pilot feedback follow-ups

Addressed the four review items on the pilot results. Spot-checks came back clean → ready to scale toward 1,000 threads.

### Spot-check: `not_a_critique` filter (no code change — verified)
- Of 254 excluded messages, **164 are OP self-replies** (structural, safe) and only 90 are LLM-labeled exclusions.
- Read a 30-message sample of the LLM exclusions: all genuinely non-critiques (career questions like "how did you get amazon first year", banter, application advice unrelated to the resume document). **No real critiques being discarded** — filter is trustworthy at scale.

### `src/knowledge/exploration.py` — real critique-volume stat
- Finding #4 previously counted raw messages (mean 5.4/thread) including excluded ones. Now computes **real critiques per thread post-filter**: mean **3.3**, median 2, max 22; raw-message mean kept as a reference figure. Histogram (`03_critiques_per_thread.png`) now plots real counts.

### `src/knowledge/norms.py` — year extraction (verified mostly data limitation)
- Investigated the 57 unknown-year threads: **0** had year info lost between raw post and structured record; only 12 had "yearish" text at all, mostly ambiguous ("first co-op" ≠ a school year).
- Regex fixes for the real misses: hyphenated forms ("Third-year"), "recent graduate", "graduate in <month> 2025" → `new_grad`. Unknown rate 48% → **45%**; the remainder genuinely never state a year ("summer 2025 co-op"). Conclusion: data limitation, handled at retrieval instead (below).

### `docs/phases/phase3-knowledge-base.md` — two retrieval design decisions recorded
- **General-blend:** don't filter out `general`-section critiques (45% of real ones, often highest-value); blend e.g. 5 section-specific + 3 general per query.
- **Unknown-year soft match:** `year=unknown` gets neutral partial credit in `profile_match`, never a mismatch penalty.

### Regenerated + docs
- `norms.json`/`norms.db`, `FINDINGS.md`, figs, `critique_labels.jsonl` re-run. Gates still pass: bullets median 2, other 14.9% (LLM labeling is slightly stochastic run-to-run; was 13.8%), survival 98.3%. 29 tests passing.
- `CLAUDE.md`: added the standing rule to append to this changelog after each session.

### Suggested commits
- `fix(phase2): report post-filter critique volume; year regex for hyphenated/grad phrasings`
- `docs(phase3): record general-blend + unknown-year soft-match retrieval decisions`
- `docs: add CHANGELOG convention`

---

## 2026-07-11 — Pre-Phase-3 data-quality fixes

Fixed the four problems surfaced by the 120-thread pilot; regenerated norms + FINDINGS. All three gates pass: bullets median **2** (gate 2–4), critique other **13.8%** (gate <15%), survival **98.3%** (gate ≥85%).

### `src/knowledge/norms.py`
- `BULLET_RE` now matches `● ◦ ▪ ‣ ○ – —` (was missing `●`, the most common PDF glyph).
- New `_split_entries()`: splits experience/projects sections on **date-line boundaries** (`Jan 2024 – May 2024`) instead of blank lines, with blank-line and bullet-run fallbacks. Fixes bullets-per-entry median 9 → 2.
- `infer_year_label()` rewritten: Waterloo term codes map to school years (`1B → year_1`, `2A → year_2`), added "2nd year"/"masters"/"grad student" phrasings, `intern` is no longer a year label.
- New `has_internships()` signal used by bucketing instead of the old intern-as-year hack.
- `role_bucket()`: frontend/backend/fullstack fold into `swe` (documented in docstring; `schemas.py` TargetRole untouched).
- CLI: `--debug-bullets N` prints per-entry bullet counts for N resumes for eyeball verification.

### `src/knowledge/exploration.py`
- OP self-replies filtered via `data/raw/dataset.json` author join (`load_op_authors`, `is_op_reply`) → tagged `not_a_critique`/`op_reply`, excluded from category stats (113 of 419 pilot critiques).
- Expanded taxonomy: added `tailoring`, `redundancy_filler`, `links_portfolio`, `ats_formatting`, `project_selection`, `positive_feedback`, `not_a_critique`.
- LLM labeling pass: batched `gpt-4o-mini` prompts (15 critiques/call, strict JSON array, heuristic fallback per item, G3 `--yes` gate). `label_source` recorded per row.
- New `other_rate()` helper (excludes `not_a_critique`); FINDINGS reports it against the <15% gate.

### `src/ingestion/pdf_extract.py`
- Tesseract installed via Homebrew (5.5.2); `tesseract_available()` + `ensure_tessdata_prefix()` make the existing PyMuPDF OCR fallback actually fire.
- New `--triage` CLI: buckets extraction failures (image_file / image_only_pdf / corrupted / …) → `data/structured/extraction_triage.json`. Current corpus: 120/120 ok.

### `src/ingestion/structure.py`
- New `--only-missing` incremental mode: loads existing output thread_ids, structures only missing ones, appends. Used to recover the 40 OCR-blocked threads (38 succeeded, 2 LLM JSON failures) without re-paying for the 80 done.

### Data / docs / tests
- Regenerated: `data/structured/structured.jsonl` + `threads.jsonl` (118), `data/norms/*`, `notebooks/FINDINGS.md` + figs + `critique_labels.jsonl`.
- Updated `docs/phases/phase1-ingestion.md`, `phase2-exploration-norms.md`, `CLAUDE.md` phase table.
- New `tests/test_exploration.py`; extended `tests/test_norms.py` (29 passing).
- `.gitignore` hardened: `venv/` (was untracked-but-not-ignored, ~1.9GB!), pytest/mypy caches, `.env.*`, global `.DS_Store`.

### Suggested commits
- `fix(phase2): bullet parsing via date-line entry segmentation + ● glyph`
- `feat(phase2): critique taxonomy expansion + OP-reply filter + gpt-4o-mini labeling`
- `feat(phase1): Tesseract OCR recovery, --triage report, --only-missing structuring`
- `fix(phase2): profile taxonomy — term codes to school years, role bucket folding`
- `docs: regenerate FINDINGS + norms, update phase statuses`
- `chore: harden .gitignore (venv, caches, env files)`
