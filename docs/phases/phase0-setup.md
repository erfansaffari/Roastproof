# Phase 0 — Setup & Data Audit

## Objective

Working repo skeleton, frozen holdout, documented understanding of raw data shape.

## Prerequisites

None — this is the first phase. Requires Erfan to have populated `data/raw/` with scraped threads before the audit script can run meaningfully.

## Inputs

`data/raw/` populated by Erfan with scraped threads. **First task: inspect the actual on-disk format** (directory-per-thread? one JSON per thread? PDFs alongside a messages file?) and document it in `NOTES.md`. All Phase 1 parsers must be written against the real format, not an assumed one. **If the format is ambiguous, stop and ask Erfan before proceeding.**

## Tasks

1. **Scaffold the repo** per the structure in `CLAUDE.md` §Repository Structure.
   - `pyproject.toml` with dependencies from the tech stack list.
   - `.gitignore` covering `data/raw`, `data/holdout`, `logs/`, `.env`, `data/chroma`.
2. `src/llm.py` — shared client wrapper (G5):
   - `complete(prompt, model, phase, max_tokens, system=None) -> str`
   - `complete_json(prompt, model, phase, schema: Type[BaseModel]) -> BaseModel` — parses, validates, retries once on failure.
   - `batch_complete(requests, model, phase)` — Batch API helper.
   - All paths log `{ts, phase, model, prompt, response, usage}` to `logs/llm_calls.jsonl`.
3. `scripts/audit_raw.py` — counts threads, PDFs, messages; prints 5 random thread samples; runs the G2 PII regex sweep in report-only mode and prints hit counts.
4. `scripts/make_holdout.py` — seeded random selection (seed=42) of 100 threads moved to `data/holdout/`; writes `data/holdout/MANIFEST.json` with thread IDs. Refuses to run twice.
5. **Human step (Erfan):** read 20 random threads, record observations in `NOTES.md` (critique phrasing patterns, junk types, reply structure). Claude Code generates the 20-thread sample list to make this easy.

## Acceptance Criteria

- [ ] `pytest` runs green (wrapper unit tests with mocked API).

- [ ] `audit_raw.py` runs end-to-end on real data and prints a sane summary.

- [ ] Holdout frozen with manifest; re-running the script errors out.

- [ ] `NOTES.md` contains the raw-format description + ≥5 data observations.

## Human Sign-Off Gate

Erfan must review and approve `NOTES.md` (raw format description + 20-thread observations) before Phase 1 ingestion parsers are written against it.

## Do Not Proceed

Do not start Phase 1 until `NOTES.md` is signed off and all four acceptance criteria above are checked.