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
- No scraping code — data is already collected and anonymized; the pipeline starts from local files.

## Hard Guardrails (apply to ALL phases — non-negotiable)
- **G1 — No fabrication.** The system must NEVER invent skills, tools, metrics, numbers, dates, titles, or experiences the user did not provide. Gaps are surfaced as *suggestions* in the report, never silently added.
- **G2 — PII hygiene.** Data is pre-anonymized, but every ingestion step runs a regex PII sweep (emails, phone numbers, URLs with usernames) and redacts on match; log redaction counts.
- **G3 — Cost control.** Bulk per-thread work uses `claude-haiku-4-5-20251001` via the Batch API. Synthesis/generation uses `claude-sonnet-4-6`. Never use Opus. Every script prints estimated token counts before a run of >100 API calls and requires a `--yes` flag to proceed.
- **G4 — Holdout isolation.** `data/holdout/` is read ONLY by Phase 6 eval code. Add a CI-style check (grep in tests) that no module outside `src/eval/` imports or opens holdout paths.
- **G5 — Logging.** Every LLM call appends `{ts, phase, model, prompt, response, usage}` to `logs/llm_calls.jsonl` via a single shared client wrapper in `src/llm.py`. No raw `anthropic` client usage anywhere else.

## Tech Stack
- Python 3.11+, managed with `uv` (or pip + venv), `pyproject.toml`.
- PDF text: **PyMuPDF** (primary) + **pdfplumber** (comparison fallback).
- Data: pandas, SQLite (`data/norms.db`), JSONL as interchange format.
- Embeddings/RAG: **ChromaDB** (persistent local), embedding model `sentence-transformers/all-MiniLM-L6-v2` locally (no API cost); embedder kept swappable.
- LLM: Anthropic Python SDK. Models per G3. JSON outputs validated with **Pydantic** schemas; retry once on validation failure with the error appended to the prompt.
- Rendering: **Jinja2** templates → LaTeX → compile with **Tectonic** (fallback: `pdflatex`).
- UI: **Streamlit** (Phase 7).
- Tests: pytest. Each phase ships unit tests for its pure-Python logic (parsers, escapers, schema validation, retrieval formatting). LLM calls are mocked in tests.

## Repository Structure
```
roastproof/
├── pyproject.toml
├── README.md
├── NOTES.md                      # data observations (Phase 0)
├── .env.example                  # ANTHROPIC_API_KEY
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
│   ├── ingestion/  (pdf_extract.py, assemble.py, structure.py, filter.py)
│   ├── knowledge/  (rulebook.py, vectorstore.py, retrieve.py, norms.py)
│   ├── generation/ (intake.py, generator.py, renderer.py, pagefit.py, critic.py, pipeline.py)
│   ├── eval/       (harness.py, judge.py, ablations.py, report.py)
│   └── app/        (streamlit_app.py)
├── templates/jakes_resume.tex.j2
└── tests/
```

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
- Never call the Anthropic API outside `src/llm.py` (G5). Never touch `data/holdout/` outside `src/eval/` (G4).
- If the raw data format in `data/raw/` differs from assumptions, update `NOTES.md` and the ingestion design first — do not force-fit.
- Commit after each completed task with conventional-commit messages (e.g. `feat(phase1): pdf extraction with dual-engine comparison`).

## Phases
| Phase | File | Status |
|---|---|---|
| 0 — Setup & Data Audit | [docs/phases/phase0-setup.md](docs/phases/phase0-setup.md) | Not started |
| 1 — Ingestion Pipeline | [docs/phases/phase1-ingestion.md](docs/phases/phase1-ingestion.md) | Not started |
| 2 — Data Exploration & Norms | [docs/phases/phase2-exploration-norms.md](docs/phases/phase2-exploration-norms.md) | Not started |
| 3 — Knowledge Base (Rulebook + Vector Store) | [docs/phases/phase3-knowledge-base.md](docs/phases/phase3-knowledge-base.md) | Not started |
| 4 — Generation Pipeline | [docs/phases/phase4-generation.md](docs/phases/phase4-generation.md) | Not started |
| 5 — Critic Loop & Suggestions Report | [docs/phases/phase5-critic-loop.md](docs/phases/phase5-critic-loop.md) | Not started |
| 6 — Evaluation | [docs/phases/phase6-evaluation.md](docs/phases/phase6-evaluation.md) | Not started |
| 7 — Interface & Ship | [docs/phases/phase7-interface-ship.md](docs/phases/phase7-interface-ship.md) | Not started |
