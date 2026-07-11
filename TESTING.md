# Testing Guide — Phase 0 & Phase 1

Manual, self-serve guide to verify everything built so far actually works.
Covers: repo setup, the scraper, the sync step, and the ingestion pipeline
(`assemble.py` → `structure.py` → `filter.py`). Later phases (2–7) don't exist
yet, so there's nothing to test there.

Run every command from the **repo root** unless a step says otherwise.

## 0. Before you start

**Costs money / touches live services — read this first:**
- Steps 3 and 8/9 call the real Anthropic API. Haiku pilot calls are cents;
  don't run the full (`--yes`) batch path repeatedly without thinking about it.
- Step 2 (`repair` / `export`) uses your live Discord token and re-downloads
  from Discord. Safe to run, but it's a real network operation against your
  account, not a local test.
- Step 12 (holdout) **moves files** out of `data/raw/` permanently and refuses
  to run twice. Do not run it against real data until the corpus has ≥100
  threads (the PRD wants a 1,000-thread corpus with a 100-thread holdout) — if
  you're below that, it will safely refuse instead of partially running.

**One-time setup:**
```bash
cd /path/to/Roastproof
source venv/bin/activate        # or: python -m venv venv && source venv/bin/activate && pip install -e .
```
Create a `.env` at the repo root with:
```
OPENAI_API_KEY=sk-...
```
And a `.env` inside `src/scraper/` (only needed for step 2) with:
```
DISCORD_USER_TOKEN=...
RESUME_CHANNEL_ID=...
```

---

## 1. Unit tests (free, no API calls)

```bash
python -m pytest -q
```
**Expect:** `9 passed`. If anything fails, stop here — nothing downstream can
be trusted until this is green.

---

## 2. Scrape from scratch (live Discord, your token)

`src/scraper/data/` was wiped clean (fresh `bot.db`, empty `resumes/` and
`export/`) to start over cleanly after the earlier double-export bug (fixed —
see `CLAUDE.md`). `bot/config.py` recreates the needed directories and
`db.init_db()` recreates the schema automatically, so there's no setup step
beyond having a valid `.env` in `src/scraper/`.

```bash
cd src/scraper
python -m bot.main scrape --limit 20   # start small to sanity-check before a big run
python -m bot.main export
cd ../..
```
**Expect:** `scrape` logs `Resume captured: ...` lines and finishes with
`Scrape complete: N resumes added, M critiques added.` (N > 0 if the channel
has resume posts with supported attachments — png/jpg/jpeg/webp/pdf).
`export` reports `N resumes, M critiques` and writes
`src/scraper/data/export/dataset.json`. Verify PDFs actually landed this time
(this is the exact check that caught the previous bug — always run it after
`export`):
```bash
find src/scraper/data/export -name "*.pdf" -o -name "*.png" -o -name "*.jpg" | wc -l
```
Should be > 0. If it's 0 again, stop and investigate before going further —
don't assume it'll sort itself out downstream.

Re-run `scrape` (without `--limit`, or with a higher one) to pull the rest of
the channel once the small run looks correct. `export` is now idempotent
(fixed) — safe to re-run any time after scraping more.

If you don't have a Discord token handy right now, skip this — everything in
steps 4–7 still runs, it'll just show 0 threads / 0 PDFs (nothing to sync
yet).

---

## 3. Sync scraper export into `data/raw/`

```bash
python scripts/sync_scraper_data.py
```
**Expect:** `Synced <N> thread folders (+ dataset.json) into data/raw`, where
N matches however many resumes you scraped in step 2.
Verify:
```bash
ls data/raw | wc -l          # N + 1 (thread folders + dataset.json)
find data/raw -name "*.pdf"  # should now be non-empty if step 2 succeeded
```
Re-run this any time after a fresh `export` — it's non-destructive and safe
to repeat.

---

## 4. Audit the raw data (free, no API)

```bash
python scripts/audit_raw.py --path data/raw
```
**Expect:** thread count matching what you scraped, PDF count, 5 random thread samples printed with
content previews, and a PII hit-count summary (report-only — nothing is
modified). A `PHONE` count around single digits is normal (course codes like
"CS 246" can false-positive on the phone regex — eyeball a couple of hits with
`grep -rE '\(?[0-9]{3}\)?[-. ]?[0-9]{3}[-. ]?[0-9]{4}' data/raw/*/post.txt data/raw/*/critiques.txt`
to confirm they're not real phone numbers).

---

## 5. PDF extraction comparison (free, no API)

Only meaningful once step 2 has recovered real PDFs.
```bash
python -m src.ingestion.pdf_extract --input-dir data/raw --compare 3
```
**Expect (no PDFs yet):** `No PDFs found to compare.` — this is correct, not
a bug, confirmed above.
**Expect (after recovery):** side-by-side PyMuPDF vs. pdfplumber text + scores
for 3 sample resumes. Eyeball that the extracted text looks like real resume
content, not garbage/mojibake — if it does, note which library won and why in
`NOTES.md` (this feeds Phase 0's data-observation task).

---

## 6. Assemble raw → structured input (free, no API)

```bash
python -m src.ingestion.assemble --input-dir data/raw --output-file data/structured/assembled.jsonl
```
**Expect (no attachments yet):** `Skipping <id>: no resume attachment found...`
for every thread, `Assembled 0 valid records`.
**Expect (after a successful scrape):** `Assembled <N> valid records` close to
your total thread count. Resume files keep Discord's original names
(`SWE_Resume.pdf`, `resume-1.png`, …) — `pdf_path` will point at those, not a
fixed `resume.pdf`. Inspect one record:
```bash
head -1 data/structured/assembled.jsonl | python3 -m json.tool
```
Confirm it has `thread_id`, `pdf_path` (pointing at a real file on disk),
`context_message`, and a `critiques` list with real author/content/timestamp fields.

---

## 7. Structure with the LLM — pilot mode (real API calls, cents)

Only run once step 6 produced real records.
```bash
python -m src.ingestion.structure \
  --input-file data/structured/assembled.jsonl \
  --pilot 5
```
**Expect:** a progress bar over 5 threads, then
`Structured records written to data/structured/pilot.jsonl`. Check:
```bash
wc -l data/structured/pilot.jsonl        # up to 5 (fewer if the LLM failed on some)
tail -5 logs/llm_calls.jsonl | python3 -m json.tool
```
Each log line must have `ts`, `phase`, `model` (`gpt-4o-mini`),
`prompt`, `response`, `usage`. This is your G5 (logging) and G3 (correct
model) check in one step.

**Human gate:** per the PRD, read `pilot.jsonl` yourself before running the
full batch in step 8 — confirm `target_role`, `agreement_signal`, and
`original_text` look sane against the source threads.

---

## 8. Structure with the LLM — full batch run (real API calls)

Only after the pilot looks good.
```bash
python -m src.ingestion.structure \
  --input-file data/structured/assembled.jsonl \
  --output-file data/structured/structured.jsonl
```
With ≤100 threads this runs without a `--yes` gate; above 100 it'll print a
cost estimate and require `--yes` (G3). This submits a real Anthropic Batch
job and polls until it ends — it can take longer than a synchronous call
(minutes, sometimes more), so don't assume it's hung if it sits for a bit.
**Expect:** `Submitting N threads to the Batch API`, then eventually
`Structured records written to data/structured/structured.jsonl`.

---

## 9. Filter → final threads.jsonl (free, no API)

```bash
python -m src.ingestion.filter \
  --input-file data/structured/structured.jsonl \
  --output-file data/structured/threads.jsonl \
  --raw-path data/raw \
  --assembled-path data/structured/assembled.jsonl
```
**Expect:** a funnel report — Raw Threads → Assembled → Structured → Final
Clean — plus a survival-rate percentage. The PRD's Phase 1 acceptance
criterion is ≥85%; below that, investigate before trusting the output.

---

## 10. Schema validators (free, no API, pure Python)

Confirms the one-page/fabrication-adjacent constraints in `src/schemas.py`
actually reject bad data instead of silently accepting it:
```bash
python3 - <<'EOF'
from src.schemas import ResumeContent
from pydantic import ValidationError

good = {
    "contact": {"name": "Jane Doe", "email": "jane@example.com"},
    "education": [{"school": "Waterloo", "degree": "BCS"}],
    "experience": [{"title": "SWE Intern", "org": "Acme",
                     "bullets": ["Built a caching layer that cut p95 API latency by 40 percent under peak load."]}],
    "projects": [],
    "skills": {"languages": ["Python"]},
    "section_order": ["education", "experience", "projects", "skills"],
}
ResumeContent(**good)
print("OK: valid resume passed")

bad = dict(good)
bad["experience"] = [{"title": "SWE Intern", "org": "Acme", "bullets": ["too short"]}]
try:
    ResumeContent(**bad)
    print("BUG: should have raised on short bullet")
except ValidationError:
    print("OK: correctly rejected a too-short bullet")
EOF
```
**Expect:** both `OK:` lines, no `BUG:` line.

---

## 11. Guardrail static checks (free, no API)

**G5 — no raw `openai` client usage outside `src/llm.py`:**
```bash
grep -rln "OpenAI(" src/ | grep -v "src/llm.py"
```
**Expect:** no output (empty = pass).

**G4 — nothing outside `src/eval/` touches `data/holdout/`** (there's no
`src/eval/` yet, so this should currently be empty too):
```bash
grep -rln "data/holdout" src/ | grep -v "src/eval/"
```
**Expect:** no output.

---

## 12. Holdout creation — do not run for real yet

```bash
python scripts/make_holdout.py --raw-path data/raw --holdout-path /tmp/holdout_test --num-threads 100
```
**Expect right now:** `Error: Not enough threads. Found <N>, but require 100.`
— it refuses safely without touching anything (confirmed behavior). Once the corpus
has ≥1,000 threads, only THEN run it against the real `data/raw/`
(`--holdout-path data/holdout`, no `/tmp` override), exactly once. If you want
to test the "refuses to run twice" behavior before that, do it against a
scratch copy of `data/raw/`, never the real one.

---

## Quick pass/fail checklist

| # | Check | Pass condition |
|---|---|---|
| 1 | `pytest` | `9 passed` |
| 2 | `scrape` / `export` | ≥1 PDF captured (or knowingly skipped) |
| 3 | sync script | `data/raw/` has N thread folders + dataset.json |
| 4 | `audit_raw.py` | runs end-to-end, sane counts |
| 5 | `pdf_extract.py --compare` | readable text once PDFs exist |
| 6 | `assemble.py` | record count > 0 once PDFs exist |
| 7 | `structure.py --pilot` | pilot.jsonl written, log entries correct model |
| 8 | `structure.py` full | threads batch through Batch API, not sync loop |
| 9 | `filter.py` | survival rate ≥ 85% |
| 10 | schema validators | rejects invalid bullets, accepts valid ones |
| 11 | guardrail greps | both empty |
| 12 | `make_holdout.py` | refuses safely below 100 threads |

If every row passes, Phase 0 and Phase 1's acceptance criteria (see
`docs/phases/phase0-setup.md` / `phase1-ingestion.md`) are met except for the
two genuinely human tasks: writing `NOTES.md` and growing the corpus toward
~1,000 threads.
