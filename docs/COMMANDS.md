# Roastproof — Command Cheat Sheet

Simple, copy-paste reference for every command. **The one you use daily is #1.**

Run everything from the repo root. Use the project's Python (`./.venv/bin/python`)
so dependencies resolve. Examples below assume that; plain `python` also works if
your venv is activated.

---

## 1. Generate a resume (the main command)

Turn an intake YAML into a one-page PDF + review report.

```bash
./.venv/bin/python -m src.generation.pipeline --intake examples/my_intake.yaml --out out/mine
```

**What it does, in order:**
1. **Stage A — Input review:** asks clarifying questions (written to `examples/my_intake.qa.yaml`).
2. Generates the resume.
3. Fits it to one page (may add "tell me more" questions if the page is thin).
4. **Stage B — Output review:** critic rewrites weak bullets → evaluates your projects → flags missing skills.
5. Writes the report.

**The two required-ish flags:**

| Flag | Meaning | Default |
|---|---|---|
| `--intake PATH` | Your intake YAML. **Required.** | — |
| `--out DIR` | Where outputs go. | `out` |

### Answering questions (the normal loop)
1. Run the command above.
2. Open the sidecar it created: `examples/my_intake.qa.yaml`.
3. Fill in each `answer:` field. To skip one, set its answer to `skip`.
4. **Re-run the exact same command.** It regenerates with your new facts and re-reviews.

Repeat until it says `CONVERGED` and the page fill is good.

### Optional flags (only if you need them)

| Flag | What it does | Default |
|---|---|---|
| `--skip-elicit` | Don't ask questions this run (just generate + review). | off |
| `--elicit-only` | ONLY ask questions, don't generate a resume. | off |
| `--skip-critic` | Skip the Phase 5 bullet-rewrite critic loop. | off |
| `--skip-project-eval` | Skip the project-portfolio evaluation. | off |
| `--skip-pagefit` | Don't resize to one page (render as-is). | off |
| `--max-elicit-rounds N` | Stop asking questions after N rounds. | 3 |
| `--max-critic-rounds N` | Stop critic rewrites after N rounds. | 2 |
| `--fill-target F` | Target page fullness (0–1). Higher = fuller page. | 0.85 |
| `--prev-eval PATH` | Prior `project_eval.json` for stable verdicts. | `out/project_eval.json` |

### Common recipes

```bash
# First real run (asks questions, generates, reviews)
./.venv/bin/python -m src.generation.pipeline --intake examples/my_intake.yaml --out out/mine

# Just see what questions it would ask — no resume
./.venv/bin/python -m src.generation.pipeline --intake examples/my_intake.yaml --out out/mine --elicit-only

# Fast regenerate without new questions (you already answered them)
./.venv/bin/python -m src.generation.pipeline --intake examples/my_intake.yaml --out out/mine --skip-elicit

# Fuller page
./.venv/bin/python -m src.generation.pipeline --intake examples/my_intake.yaml --out out/mine --fill-target 0.92
```

### What you get in the `--out` folder

| File | What it is |
|---|---|
| `resume.pdf` | The compiled one-page resume. |
| `resume.tex` | LaTeX source (for debugging). |
| `content.json` | The resume as structured data. |
| `report.md` | **Read this.** Two-stage review: questions + resume feedback. |
| `suggestions.json` | Missing skills + project-portfolio verdicts. |
| `critic.json` | Remaining bullet-quality issues (grounded). |
| `revision_log.json` | Before/after of every bullet the critic rewrote. |
| `project_eval.json` | Project portfolio evaluation. |
| `status.json` | Run summary (converged? page fill? critic rounds?). |
| `questions.json` | Snapshot of the current Q&A. |

The Q&A sidecar (`*.qa.yaml`) sits next to your **intake**, not in `--out`.

---

## 2. Run the tests

```bash
# All tests
./.venv/bin/python -m pytest -q

# One file
./.venv/bin/python -m pytest tests/test_phase5_critic.py -q
```

---

## 3. Data collection (scraper) — only when gathering new data

Standalone Discord scraper. Needs a live Discord token. Run from `src/scraper/`.

```bash
cd src/scraper

python -m bot.main scrape            # scrape the resume channel
python -m bot.main scrape --limit 50 # scrape at most 50 threads
python -m bot.main export            # write data/export/dataset.json
python -m bot.main repair            # re-download attachments lost by an old bug
```

After exporting, copy it into the pipeline's data dir:

```bash
cd ../..                                   # back to repo root
./.venv/bin/python scripts/sync_scraper_data.py
```

---

## 4. Build the knowledge base (one-time / when data changes)

Run these in order after new data lands in `data/raw/`.

```bash
# Step 1 — Ingestion: raw files -> clean structured threads
./.venv/bin/python -m src.ingestion.pdf_extract      # extract text/OCR from PDFs & images
./.venv/bin/python -m src.ingestion.assemble         # combine into assembled.jsonl
./.venv/bin/python -m src.ingestion.structure        # LLM-structure into threads.jsonl
./.venv/bin/python -m src.ingestion.filter           # drop low-quality threads

# Step 2 — Norms & exploration (stats used for skill prevalence)
./.venv/bin/python -m src.knowledge.norms
./.venv/bin/python -m src.knowledge.exploration

# Step 3 — Knowledge base (the rulebook + searchable critique store)
./.venv/bin/python -m src.knowledge.rulebook         # build rulebook.json
./.venv/bin/python -m src.knowledge.vectorstore      # build the Chroma vector store
./.venv/bin/python -m src.knowledge.style_mine       # mine fluff/banned phrases
./.venv/bin/python -m src.knowledge.rewrite_mine     # mine before->after rewrite examples
```

> Many of these hit the OpenAI API in bulk. Scripts that make >100 calls print an
> estimate first and require a `--yes` flag to actually run (cost guardrail G3).

---

## 5. Utility scripts

```bash
# Check the raw data folder is healthy
./.venv/bin/python scripts/audit_raw.py

# Freeze 100 threads as the eval holdout (eval-only data)
./.venv/bin/python scripts/make_holdout.py

# Copy scraper export into data/raw/
./.venv/bin/python scripts/sync_scraper_data.py

# Sanity-check retrieval with 10 canned queries
./.venv/bin/python scripts/test_retrieval.py

# Pick random threads for manual review
./.venv/bin/python scripts/generate_sample_list.py
```

Add `-h` to any command to see its full help, e.g.:

```bash
./.venv/bin/python -m src.generation.pipeline -h
```

---

## Quick mental model

- **You mostly only need command #1.**
- Edit intake YAML → run → answer the `*.qa.yaml` questions → re-run → read `report.md`.
- Commands #3 and #4 are for *building the corpus*, not for making a resume — you run them rarely.
