# Phase 0 — Setup & Data Audit

## Objective

Working repo skeleton, frozen holdout, documented understanding of raw data shape.

## Prerequisites

None — this is the first phase. Requires Erfan to have populated `data/raw/` with scraped threads before the audit script can run meaningfully.

## Inputs

`data/raw/` populated by Erfan with scraped threads. **First task: inspect the actual on-disk format** (directory-per-thread? one JSON per thread? PDFs alongside a messages file?) and document it in `NOTES.md`. All Phase 1 parsers must be written against the real format, not an assumed one. **If the format is ambiguous, stop and ask Erfan before proceeding.**

### Raw format — resolved
Erfan built a Discord scraper (`src/scraper/`, self-contained utility, not a phase deliverable — see `CLAUDE.md` § Data Collection) that produces the raw data. Its export (`python -m bot.main export`) writes to `src/scraper/data/export/`, in this shape:
```
export/
├── dataset.json                    # canonical structured source — one object per thread:
│                                    #   {resume_message_id, author, posted_at, post_message,
│                                    #    resume_files: [...], critiques: [{author, content, timestamp}]}
└── {resume_message_id}/
    ├── <original Discord filename>.pdf|.png|.jpg|.webp
    │                                # scraper keeps the attachment's real name
    │                                # (e.g. SWE_Resume.pdf, resume-1.png) — NOT a
    │                                # fixed resume.pdf. Absent when resume_files is [].
    ├── post.txt                    # human-readable: "[<ISO ts>] <author> (resume post):\n<content>"
    └── critiques.txt               # human-readable: one "[<ISO ts>] <author>: <content>" block per
                                     #   critique, blocks separated by a blank line
```
**Decision needed from Erfan:** ingest from `dataset.json` directly (structured, no text parsing needed, includes timestamps) rather than re-parsing `post.txt`/`critiques.txt`. This is simpler and more robust than text parsing and is the recommended default — flag if you want the `.txt` files used instead.

**Resolved (file naming):** `assemble.py` resolves the resume via `dataset.json`'s `resume_files` basenames (with a directory scan fallback). Do not assume `resume.pdf` — that name never appears on disk unless Discord happened to upload a file called that.

**Resolved:**
- `scripts/sync_scraper_data.py` copies the scraper's export into `data/raw/` (dataset.json + per-thread folders). Run it after every `python -m bot.main export`. Kept as an explicit separate step rather than pointing ingestion at `src/scraper/data/export/` directly — see the script's docstring for why (frozen input contract vs. live working dir).
- `audit_raw.py` and `make_holdout.py` had unrelated syntax errors (corrupted string literals) that made them uninvocable; fixed. `audit_raw.py` now runs end-to-end against real synced data.
- `pytest` is green (was blocked by a syntax error in `schemas.py` plus the same corruption in `pdf_extract.py`/`filter.py`; also fixed one test with an incorrect assertion).

**Still open, blocking full Phase 0 sign-off:**
- `NOTES.md` has not been written yet — required before Phase 1 parsers are finalized and before the human sign-off gate below can be closed. This is a human task (read 20 real threads, record observations) — not something to generate synthetically.
- **Data-loss bug found and fixed, but the damage to the current 20-thread batch is not yet repaired.** All 20 scraped resumes are missing their PDF/image attachment locally — `dataset.json` shows `resume_files: []` for every entry, and `src/scraper/data/resumes/` is empty. Root cause: `export.py` used to `shutil.rmtree(EXPORT_DIR)` on every run, but re-exporting after the *first* export had already deleted the original source files (by design — export is the canonical copy) meant the second `rmtree` destroyed the only remaining copies with no way to recover them from disk. **Fixed** so export is now idempotent (checks the destination before assuming a file is missing; no longer wipes the export dir). To actually recover the 20 already-lost files, run (from `src/scraper/`, needs a live Discord token — I cannot run this myself):
  ```
  python -m bot.main repair   # re-fetches the 20 messages, re-downloads attachments
  python -m bot.main export   # rewrites dataset.json / per-thread folders with the recovered files
  ```
  then re-run `scripts/sync_scraper_data.py` from the repo root to pull the recovered PDFs into `data/raw/`. This only works if the original Discord messages/attachments are still live (nothing was deleted from Discord itself — only local copies were lost).
- Only 20 threads have been scraped so far, well short of the ~1,000-thread target — expected to grow as scraping continues, not a blocker for writing the pipeline itself.

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