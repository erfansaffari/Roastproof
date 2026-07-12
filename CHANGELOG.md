# Changelog

Session-by-session record of codebase changes, for writing git commits.
Newest entries at the top. Suggested commit messages included per entry.

---

## 2026-07-12 — Phase 4.7 elicitation memory, convergence, stable project eval

Persistent Q&A sidecar + semantic dedup + stopping rule; project-eval stability.

### QA sidecar
- `src/generation/qa_store.py` — `*.qa.yaml` next to intake; stable content-hash
  ids; statuses pending/answered/declined (`skip`); legacy `intake.answers` merge.
- User edits `answer:` in place — no more copying q-ids into intake YAML.

### Elicitation
- Full prior Q&A history in `elicit_user` (not just ids); `impact` + `complete`
  on `ElicitationResult`; MiniLM semantic dedup (~0.8); rounds 2+ high-only;
  `--max-elicit-rounds` (default 3); temperature=0.
- `status.json` + printed convergence verdict every run.

### Generator / project eval
- Answers block renders full Q→A pairs; declined topics suppress repeat
  `missing_metric` suggestions.
- Prior-eval memory, `portfolio_composition`, verbatim `evidence_quote`
  validation for field gaps; temperature=0 for eval.

### Schemas / LLM
- `QAStore`, `QAEntry`; `ElicitationQuestion.impact`; `ElicitationResult.complete`;
  `FieldGap.evidence_quote`; `PortfolioCompositionItem`.
- `complete` / `complete_json` accept optional `temperature`.

### Tests / docs
- `tests/test_elicit_memory.py` (sidecar, dedup, stop, declined suppress, quotes).
- Updated `docs/phases/phase4.6-corpus-writer.md` (4.7 follow-on section).

### Suggested commits
- `feat(phase4.7): elicitation QA sidecar with memory and convergence`
- `feat(phase4.7): stable project eval with evidence quotes`

---

## 2026-07-11 (g) — Phase 4.6 corpus-derived writer knowledge

De-hardcode fluff/few-shots; professional hiring-reviewer prompts; project eval.

### Prompt library
- `src/generation/prompts.py` — big-tech 30s-screen persona, role profiles
  (SWE/ML/AI/FE/BE/…), data-wins clause; wired into generator, elicit,
  project eval, miners, page-fit trim.

### Mined artifacts
- `src/knowledge/style_mine.py` → `data/knowledge/style_lexicon.json`
  (55→filtered banned/preferred with thread ids).
- `src/knowledge/rewrite_mine.py` → `data/knowledge/rewrite_examples.json`
  (filtered action-bullet pairs for few-shots).
- `fluff.py` loads lexicon; seed list is fallback; refuses noisy nouns.

### Project eval + skill gaps
- `src/generation/project_eval.py` — grounded verdicts + field gaps;
  suggestion type `project_evaluation`; critique ids in prompt.
- Tiered missing-skill suggestions: core (>50%) / common (25–50%) with
  bucket disclosure.

### Tests / docs
- `tests/test_phase46.py` + updated generation tests (23 passed).
- `docs/phases/phase4.6-corpus-writer.md`

### Suggested commits
- `feat(phase4.6): prompt library, style/rewrite mining, project eval`
- `feat(phase4.6): tiered corpus skill-gap suggestions`

---

## 2026-07-11 (f) — Phase 4.5 writer quality

Generator was formatting intake into LaTeX; now forced to **rewrite** against
community rules. Log check showed rules/critiques/norms *were* injected —
failure was framing + skill matching + no fluff lint.

### Fixes
- Prompt: facts-frozen / wording-mandatory; few-shot before→after; annotated
  bullets `{text, rewritten_from, gaps}` → `missing_metric` suggestions.
- Skills gap: `expand_skill_tokens` so `Git/GitHub Actions CI` owns `Git`;
  rebuild missing_skill suggestions from final owned set with prevalence %;
  drop LLM "showcase breadth" padding.
- `fluff.py` banned-word lint + one generator retry; pytest coverage.
- `elicit.py` pre-pass → `questions.json`; intake `answers:` for second run.
- CLI: `--elicit-only`, `--skip-elicit`.

### Live check (`examples/my_intake.yaml` → `out/mine/`)
- Git no longer suggested (compound skill present).
- `missing_metric` emitted for unquantified bullets.
- 6 elicitation questions written; SchoolTalk bullets rewritten with concrete
  schema/RBAC detail instead of "robust…ensure".

### Docs
- `docs/phases/phase4.5-writer-quality.md`

### Suggested commits
- `feat(phase4.5): writer prompt, annotated bullets, fluff lint, elicitation`
- `fix(phase4): compound skill matching for norms gap suggestions`

---

## 2026-07-11 (e) — Phase 4 generation pipeline

End-to-end: YAML intake → gpt-4o `ResumeContent` → Jake's LaTeX → one-page PDF.

### Schemas / deps
- `src/schemas.py`: `ResumeContent` bullet validators restored; added `Intake*`,
  `Suggestion`, `GenerationResult`.
- `pyproject.toml`: added `PyYAML`.
- `.gitignore`: `out/`.

### Generation package (`src/generation/`)
- `intake.py` — YAML/JSON → `Intake`.
- `generator.py` — rules (≤20) + `retrieve()` per section + norms prevalence;
  G1 system prompt; deterministic `enforce_g1()` strips fabricated skills and
  pushes high-prevalence absences into suggestions.
- `renderer.py` — `latex_escape` + Jinja Jake template → Tectonic PDF (no LLM).
- `pagefit.py` — ≤3 LLM trim attempts, then deterministic hard-trim.
- `pipeline.py` — `python -m src.generation.pipeline --intake … --out …`.

### Assets / examples
- `templates/jakes_resume.tex.j2` (Jake's layout; `{% raw %}` for `#` macros).
- `examples/intake_example.yaml` (omits Git for G1 demo).
- `examples/intake_overstuffed.yaml` (page-fit stress).

### Tests / live runs
- `tests/test_generation.py` — 9 passed (escape, G1 fabrication, hard-trim→1 page).
- Live: `out/example/resume.pdf` (1 page); Git in suggestions only, not skills.
- Live: `out/overstuffed/resume.pdf` (1 page).

### Suggested commits
- `feat(phase4): intake → generator → Jake LaTeX → one-page PDF pipeline`
- `test(phase4): latex_escape, G1 fabrication, page-fit hard-trim`

---

## 2026-07-11 (d) — Retrieval QA fixes (pre–Phase-4)

Three concrete issues from the retrieval QA review, plus role fallback.

### `src/knowledge/retrieve.py`
- Exclude `positive_feedback` at query time (`EXCLUDED_RETRIEVAL_CATEGORIES`) —
  kept in the store, filtered out of generation retrieval (fixes Q5/Q9 fluff).
- Cap agreement at 3 before normalizing (`normalize_agreement`); viral
  agree=9 no longer outranks agree=3.
- Dedupe final top-k by `thread_id` so one thread cannot fill multiple slots.
- `ROLE_FALLBACK` map: ML / data / frontend / … also query Software Engineer
  when the primary bucket is thin.
- `format_for_prompt(..., max_chars=)` optional display-only truncation;
  generator path keeps full text.

### `src/knowledge/vectorstore.py`
- Store **full** `issue` in Chroma metadata (was `[:500]` — caused mid-word
  cuts in the QA output). Embedding composite still capped at 2000 chars.

### Docs / tests
- Cleared rubber-stamp grades from `NOTES.md`; sheet blank for Erfan's own pass.
- Unit tests updated for agreement cap + role fallback (9 phase3 tests).
- Rebuilt `critiques_v1` (388 points).

### Suggested commits
- `fix(phase3): exclude positive_feedback; cap agreement; thread-id dedupe`
- `fix(phase3): store full critique text; add role_fallback for thin buckets`

---

## 2026-07-11 (c) — Phase 3 knowledge base (rulebook + Chroma + retrieve)

Implemented Phase 3 on the 118-thread pilot. Human gate still open: Erfan must
grade `scripts/test_retrieval.py` ≥8/10 in `NOTES.md` before Phase 4.

### `src/schemas.py`
- Added `ApplicantProfile`, `CritiquePoint`; `Rule.supporting_thread_ids` for the
  hallucination guard.

### `src/knowledge/rulebook.py` (new)
- Map/reduce on `gpt-4o`: batches of 25 → candidates → merge.
- Robust JSON salvage for alternate model shapes; deterministic
  `anchor_rules_to_corpus` keyword/evidence matching; `verify_rule_evidence`
  drops fake thread_ids and enforces min frequency.
- Pilot auto-uses `min_frequency=5` when n&lt;200 (PRD default 10 otherwise).
- Output: `data/knowledge/rulebook.json` — **29 rules** (all evidence-checked).

### `src/knowledge/vectorstore.py` (new)
- Explodes threads (+ Phase-2 labels) → CritiquePoints; skips empty /
  `not_a_critique`. Composite string per PRD. Chroma `critiques_v1` with local
  `all-MiniLM-L6-v2`. `--rebuild` wipes. **388 points** indexed.

### `src/knowledge/retrieve.py` (new)
- `retrieve(profile, section, query_text, k)` with re-rank
  `0.7·sim + 0.2·profile_match + 0.1·agreement`.
- General-blend (5 section + 3 general) and unknown-year soft match (0.5 year
  credit, never a hard mismatch).
- `format_for_prompt()` numbered block for generators.

### Tests / scripts / docs
- `tests/test_phase3_knowledge.py` — composite, explode, profile_match, rerank,
  evidence guard, format_for_prompt (36 tests total suite).
- `scripts/test_retrieval.py` — 10 canned queries for Erfan grading.
- `NOTES.md` grading sheet; phase3 + CLAUDE.md status updated.

### Suggested commits
- `feat(phase3): rulebook map/reduce + evidence check`
- `feat(phase3): Chroma critiques_v1 vector store with MiniLM`
- `feat(phase3): retrieve API with general-blend and unknown-year soft match`
- `test(phase3): unit tests + canned retrieval QA script`

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
