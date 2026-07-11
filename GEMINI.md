# GEMINI.md - Roastproof Project Guide

This file serves as a guide for interacting with the Roastproof project.

## Project Overview

Roastproof is a community-grounded resume generator. It takes raw user experience and generates a one-page PDF resume and a suggestions report. The generation is grounded in a corpus of ~1,000 real community critiques.

### Goals
- End-to-end pipeline: raw user info in → compiled one-page PDF + suggestions report out.
- Measurable improvement of RAG+rulebook generation over a bare-prompt baseline.
- Every LLM interaction is logged for debugging and future fine-tuning data.

### Tech Stack
- **Python:** 3.11+
- **Package Management:** uv (or pip + venv), pyproject.toml
- **PDF Processing:** PyMuPDF, pdfplumber
- **Data Handling:** pandas, SQLite, JSONL
- **Embeddings/RAG:** ChromaDB, sentence-transformers/all-MiniLM-L6-v2
- **LLM:** Anthropic Python SDK
- **Rendering:** Jinja2, Tectonic (or pdflatex)
- **UI:** Streamlit
- **Testing:** pytest

### Repository Structure
The repository is structured as follows:
```
roastproof/
├── pyproject.toml
├── README.md
├── NOTES.md
├── .env.example
├── data/
│   ├── raw/
│   ├── holdout/
│   ├── structured/threads.jsonl
│   ├── knowledge/rulebook.json
│   ├── chroma/
│   └── norms/
├── logs/llm_calls.jsonl
├── notebooks/01_exploration.ipynb
├── src/
│   ├── llm.py
│   ├── schemas.py
│   ├── scraper/          # standalone Discord data-collection utility, not a pipeline phase
│   ├── ingestion/
│   ├── knowledge/
│   ├── generation/
│   ├── eval/
│   └── app/
├── templates/jakes_resume.tex.j2
└── tests/
```

### Data Collection (Scraper)
`src/scraper/` scrapes the Discord resume-critique channel and exports to its own `data/` tree (`src/scraper/data/export/dataset.json` plus one folder per thread with `resume.pdf` (if present), `post.txt`, `critiques.txt`). `dataset.json` is the preferred structured ingestion source; the `.txt` files are human-readable renderings only, formatted `[ISO timestamp] author: content` (not `author::content`). `scripts/sync_scraper_data.py` copies this export into `data/raw/`, the location the ingestion pipeline expects — run it after every `python -m bot.main export`.

## Development Conventions

- Work strictly phase by phase, in order.
- Pause for sign-off at marked human gates.
- Prefer small pure functions with unit tests.
- Never call the Anthropic API outside `src/llm.py`.
- Never touch `data/holdout/` outside `src/eval/`.
- Commit after each completed task with conventional-commit messages.

## Phases

The project is divided into the following phases:

| Phase | File | Status |
|---|---|---|
| 0 — Setup & Data Audit | [docs/phases/phase0-setup.md](docs/phases/phase0-setup.md) | In progress |
| 1 — Ingestion Pipeline | [docs/phases/phase1-ingestion.md](docs/phases/phase1-ingestion.md) | In progress |
| 2 — Data Exploration & Norms | [docs/phases/phase2-exploration-norms.md](docs/phases/phase2-exploration-norms.md) | Not started |
| 3 — Knowledge Base (Rulebook + Vector Store) | [docs/phases/phase3-knowledge-base.md](docs/phases/phase3-knowledge-base.md) | Not started |
| 4 — Generation Pipeline | [docs/phases/phase4-generation.md](docs/phases/phase4-generation.md) | Not started |
| 5 — Critic Loop & Suggestions Report | [docs/phases/phase5-critic-loop.md](docs/phases/phase5-critic-loop.md) | Not started |
| 6 — Evaluation | [docs/phases/phase6-evaluation.md](docs/phases/phase6-evaluation.md) | Not started |
| 7 — Interface & Ship | [docs/phases/phase7-interface-ship.md](docs/phases/phase7-interface-ship.md) | Not started |
