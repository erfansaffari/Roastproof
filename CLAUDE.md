# Roastproof — Project Guide

Community-grounded resume generator: raw user experience in → resume grounded in a ~1,000-thread corpus of real community critiques → compiled one-page PDF + suggestions report out. Full PRD: see PRD text supplied by Erfan (not stored separately — treat this file + `docs/phases/` as the working source of truth derived from it).

## Goals
- End-to-end pipeline: raw user info in → compiled one-page PDF + suggestions report out.
- Measurable improvement of RAG+rulebook generation over a bare-prompt baseline, backed by an eval harness with numbers.
- Every LLM interaction logged for debugging and future fine-tuning data.

## Non-Goals (v1)
- No fine-tuning (v2 experiment only, after the eval harness exists).
- No multi-template gallery — one primary template, Jake's-Resume style.
- No auth/user accounts. Local-first; simple Streamlit UI at the end.
- The core pipeline (Phases 1–7) does not include scraping logic — it consumes already-collected data. **Exception:** Erfan built a standalone Discord scraper (`src/scraper/`) to actually produce that data; it is a self-contained utility, not part of the phase pipeline, and is out of scope for phase acceptance criteria. See "Data Collection (Scraper)" below.

## Hard Guardrails (apply to ALL phases — non-negotiable)
- **G1 — No fabrication.** The system must NEVER invent skills, tools, metrics, numbers, dates, titles, or experiences the user did not provide. Gaps are surfaced as *suggestions* in the report, never silently added.
- **G2 — PII hygiene.** Data is pre-anonymized, but every ingestion step runs a regex PII sweep (emails, phone numbers, URLs with usernames) and redacts on match; log redaction counts.
- **G3 — Cost control.** Bulk per-thread work uses `gpt-4o-mini` (Haiku-class) via the Batch API for large runs. Synthesis/generation uses `gpt-4o` (Sonnet-class). Every script prints estimated token counts before a run of >100 API calls and requires a `--yes` flag to proceed.
- **G4 — Holdout isolation.** `data/holdout/` is read ONLY by Phase 6 eval code. Add a CI-style check (grep in tests) that no module outside `src/eval/` imports or opens holdout paths.
- **G5 — Logging.** Every LLM call appends `{ts, phase, model, prompt, response, usage}` to `logs/llm_calls.jsonl` via a single shared client wrapper in `src/llm.py`. No raw `openai` client usage anywhere else.

## Tech Stack
- Python 3.11+, managed with `uv` (or pip + venv), `pyproject.toml`.
- PDF text: **PyMuPDF** (primary) + **pdfplumber** (comparison fallback).
- Data: pandas, SQLite (`data/norms.db`), JSONL as interchange format.
- Embeddings/RAG: **ChromaDB** (persistent local), embedding model `sentence-transformers/all-MiniLM-L6-v2` locally (no API cost); embedder kept swappable.
- LLM: OpenAI Python SDK. Models per G3 (`gpt-4o-mini` bulk, `gpt-4o` synthesis). JSON outputs validated with **Pydantic** schemas; retry once on validation failure with the error appended to the prompt.
- Rendering: **Jinja2** templates → LaTeX → compile with **Tectonic** (fallback: `pdflatex`).
- UI: **Streamlit** (Phase 7).
- Tests: pytest. Each phase ships unit tests for its pure-Python logic (parsers, escapers, schema validation, retrieval formatting). LLM calls are mocked in tests.

## Repository Structure
```
roastproof/
├── pyproject.toml
├── README.md
├── NOTES.md                      # data observations (Phase 0)
├── .env.example                  # OPENAI_API_KEY
├── data/
│   ├── raw/                      # scraped threads as provided (input, git-ignored)
│   ├── holdout/                  # 100 frozen threads (git-ignored, eval-only)
│   ├── structured/threads.jsonl  # Phase 1 output
│   ├── knowledge/rulebook.json   # Phase 3 output
│   ├── chroma/                   # Phase 3 vector store
│   └── norms/                    # Phase 2 output (norms.db + norms.json)
├── logs/llm_calls.jsonl
├── notebooks/01_exploration.ipynb
├── src/
│   ├── llm.py                    # shared client wrapper (G5)
│   ├── schemas.py                # all Pydantic models
│   ├── scraper/                  # standalone data-collection utility, not a pipeline phase — see below
│   ├── ingestion/  (pdf_extract.py, assemble.py, structure.py, filter.py)
│   ├── knowledge/  (rulebook.py, vectorstore.py, retrieve.py, norms.py)
│   ├── generation/ (intake.py, generator.py, renderer.py, pagefit.py, critic.py, pipeline.py)
│   ├── eval/       (harness.py, judge.py, ablations.py, report.py)
│   └── app/        (streamlit_app.py)
├── templates/jakes_resume.tex.j2
└── tests/
```

## Data Collection (Scraper)
`src/scraper/` is a standalone Discord self-bot (`bot/` package) that scrapes the resume-critique channel and writes its own dataset independent of the main pipeline's `data/` tree:
```
src/scraper/data/
├── bot.db                      # sqlite: resumes + critiques tables
├── resumes/{message_id}/       # working download dir, emptied on export
└── export/
    ├── dataset.json            # canonical structured export, one object per thread
    └── {resume_message_id}/
        ├── <original Discord name>.pdf|.png|.jpg|.webp  # absent if resume_files is []
        ├── post.txt            # human-readable rendering of the post
        └── critiques.txt       # human-readable rendering of the critiques
```
Two representations of the same data exist after `python -m bot.main export`:
- **`dataset.json`** (preferred ingestion source) — structured JSON list; each entry has `resume_message_id`, `author`, `posted_at`, `post_message`, `resume_files` (list, can be empty), `critiques: [{author, content, timestamp}]`.
- **`post.txt` / `critiques.txt`** (human-eyeball convenience only) — plain text, one line per message formatted as `[<ISO timestamp>] <author>: <content>`, critiques separated by a blank line. **Not** the `author::content` format Phase 1's `assemble.py` currently assumes — see the Phase 1 doc for the fix needed before ingestion will actually work on this data.

`scripts/sync_scraper_data.py` copies the export into `data/raw/` (the location the ingestion pipeline and `scripts/audit_raw.py` / `make_holdout.py` expect) — run it after every `python -m bot.main export`. Kept as an explicit copy step rather than pointing ingestion straight at `src/scraper/data/export/`, since `data/raw/` is meant to be a frozen snapshot (holdout selection freezes 100 threads out of it) and the scraper's export directory is a live working area that gets rewritten on every `export` run.

As of this writing the export contains 20 threads (early scraping run), well short of the ~1,000-thread target — and all 20 are currently missing their resume PDF locally, due to a since-fixed bug in `export.py` that destroyed already-exported attachment files on a second `export` run. `bot/main.py` now has a `repair` subcommand that re-fetches the original Discord messages and re-downloads their attachments; run `repair` then `export` (needs a live Discord token) to recover them, then re-run the sync script.

## Core Data Schemas
Single source of truth: `src/schemas.py` (Pydantic), created in Phase 1. Key models:
- **ThreadRecord** — one per Discord thread: role, applicant profile, resume text/sections, context message, list of critiques, quality flags.
- **Rule** (rulebook entry) — category, section, applies_to (roles/profiles), statement, frequency, evidence examples.
- **ResumeContent** (generator output) — contact, education, experience, projects, skills, section_order. Validators: bullets 60–140 chars; ≤4 bullets/experience, ≤3/project; ≤4 projects; total bullet budget ≤22 (one-page heuristic).

Full field-level JSON shapes live in the PRD; do not restate/duplicate them elsewhere — read `src/schemas.py` once it exists.

## Execution Rules
- Work strictly phase by phase, in order. Open each phase by restating its acceptance criteria; close it by demonstrating each one is met.
- Pause for Erfan's sign-off at the marked human gates: Phase 0 NOTES.md, Phase 1 pilot review, Phase 3 retrieval QA, Phase 6 Discord posting.
- Prefer small pure functions with unit tests over monoliths; every script is also importable (logic in functions, thin `__main__`).
- Never call the OpenAI API outside `src/llm.py` (G5). Never touch `data/holdout/` outside `src/eval/` (G4).
- If the raw data format in `data/raw/` differs from assumptions, update `NOTES.md` and the ingestion design first — do not force-fit.
- Commit after each completed task with conventional-commit messages (e.g. `feat(phase1): pdf extraction with dual-engine comparison`).

## Phases
| Phase | File | Status |
|---|---|---|
| 0 — Setup & Data Audit | [docs/phases/phase0-setup.md](docs/phases/phase0-setup.md) | In progress — repo scaffolded, scraper built; NOTES.md and data/raw wiring still open |
| 1 — Ingestion Pipeline | [docs/phases/phase1-ingestion.md](docs/phases/phase1-ingestion.md) | Done on current corpus — 118/120 clean (98.3% survival) with Tesseract OCR; `--only-missing` supported |
| 2 — Data Exploration & Norms | [docs/phases/phase2-exploration-norms.md](docs/phases/phase2-exploration-norms.md) | Done — norms + FINDINGS regenerated; pre-Phase-3 gates (bullets, other&lt;15%, survival) met |
| 3 — Knowledge Base (Rulebook + Vector Store) | [docs/phases/phase3-knowledge-base.md](docs/phases/phase3-knowledge-base.md) | Not started |
| 4 — Generation Pipeline | [docs/phases/phase4-generation.md](docs/phases/phase4-generation.md) | Not started |
| 5 — Critic Loop & Suggestions Report | [docs/phases/phase5-critic-loop.md](docs/phases/phase5-critic-loop.md) | Not started |
| 6 — Evaluation | [docs/phases/phase6-evaluation.md](docs/phases/phase6-evaluation.md) | Not started |
| 7 — Interface & Ship | [docs/phases/phase7-interface-ship.md](docs/phases/phase7-interface-ship.md) | Not started |
