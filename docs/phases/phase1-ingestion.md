# Phase 1 — Ingestion Pipeline

## Objective
Produce `data/structured/threads.jsonl`: one validated `ThreadRecord` per usable thread (~900 expected).

## Prerequisites
- Phase 0 complete: repo scaffolded, `src/llm.py` working, `NOTES.md` signed off by Erfan with the real raw-data format documented.
- `src/schemas.py` defines `ThreadRecord` (and the other core schemas) as the single source of truth — create/finalize this at the start of this phase per `CLAUDE.md`.

## Status note (real-data check)
`data/raw/` is now populated via `scripts/sync_scraper_data.py` (see Phase 0). The scripts below have been fixed and updated to match the real format:
- **`assemble.py`** now reads `dataset.json` directly (structured author/content/timestamp per critique) instead of parsing `critiques.txt`'s `[<ISO timestamp>] author: content` text — the old parser assumed an `author::content` format that never matched real output, so every thread was silently dropped. It also no longer drops threads with zero critiques (that's `filter.py`'s job, per the PRD's `no_critiques` flag) — it only drops threads with no resume attachment at all, since there's nothing to structure without one. Resume paths come from `resume_files` basenames (Discord original names like `SWE_Resume.pdf` / `resume-1.png`), not a hardcoded `resume.pdf`.
- **`structure.py`** now uses `gpt-4o-mini` (OpenAI Haiku-class stand-in), and full (non-pilot) runs go through `llm.batch_complete` — OpenAI Batch API for large corpora, sync for ≤20 — instead of unbounded synchronous fan-out, per G3. Pilot mode (`--pilot N`) stays synchronous as the PRD specifies. Full runs print an estimate and require `--yes` above 100 calls.
- **`filter.py`** and **`pdf_extract.py`** had unrelated syntax errors (corrupted string literals, same issue as `schemas.py`) that made them unimportable; fixed.

**Data status (2026-07-11):** 120-thread scrape. With Tesseract installed, PyMuPDF OCR recovers image/scanned resumes (`python -m src.ingestion.pdf_extract --triage` → ok_rate 100%). `structure.py --only-missing` appends newly extractable threads without re-paying for already-structured ones. Funnel: 120 raw → 120 assembled → 118 structured → 118 clean (**98.3% survival**, above the 85% gate). Two threads failed LLM JSON validation after OCR recovery.

## Tasks
1. **`pdf_extract.py`**
   - Extract text with PyMuPDF; also with pdfplumber.
   - Heuristic quality score (fraction of lines that look like sentences/bullets vs. garbage; detect interleaved-column artifacts).
   - Pick the better extraction per file.
   - CLI: `--compare N` prints side-by-side for N samples so Erfan can eyeball.
2. **`assemble.py`**
   - Pair each PDF with its context message and critique replies into a raw JSON record (format per Phase 0 findings).
   - Strip obvious junk deterministically first (messages <15 chars, "bump", pure-emoji, bot messages).
3. **`structure.py`** — LLM structuring pass:
   - Model: Haiku, Batch API (G3).
   - Prompt outputs strict JSON matching `ThreadRecord` (minus `quality_flags`). Include 2 few-shot examples drawn from `NOTES.md` observations.
   - Instruct: normalize `target_role` to the enum; set `original_text` only when the critique clearly quotes/references specific resume text; count `agreement_signal` from echoes/reactions ("^", "this", "+1", same point restated).
   - **Pilot mode:** `--pilot 50` runs 50 threads synchronously (not batch) and writes `data/structured/pilot.jsonl` for manual review. Full run is gated behind pilot sign-off from Erfan.
   - PII sweep (G2) on all text fields post-structuring.
4. **`filter.py`**
   - Set `quality_flags`; write final JSONL excluding `parse_failed`; keep `no_critiques` and `non_cs_role` rows flagged-but-present (useful for norms).
   - Print a funnel report: raw → assembled → structured → clean counts.

## Cost Estimate
~950 threads × ~4K tokens through Haiku Batch ≈ single-digit dollars. Print estimate + require `--yes` gate per G3 before the full run.

## Acceptance Criteria
- [ ] Pilot of 50 reviewed and approved by Erfan before the full batch run.
- [x] ≥85% of raw threads survive to a valid `ThreadRecord` (118/120 = 98.3% with OCR; 2 LLM JSON failures documented).
- [x] Every record validates against the Pydantic schema; zero PII regex hits in the final file.
- [x] Unit tests: junk filter, PII sweep, section splitter, schema validation on fixture data.

## Human Sign-Off Gate
Erfan must review and approve `data/structured/pilot.jsonl` (50-thread pilot) before the full batch structuring run proceeds.

## Do Not Proceed
Do not start Phase 2 until `threads.jsonl` is finalized, the 85% survival threshold is met (or the shortfall investigated and accepted), and all acceptance criteria are checked.
