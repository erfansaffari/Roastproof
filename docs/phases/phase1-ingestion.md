# Phase 1 — Ingestion Pipeline

## Objective
Produce `data/structured/threads.jsonl`: one validated `ThreadRecord` per usable thread (~900 expected).

## Prerequisites
- Phase 0 complete: repo scaffolded, `src/llm.py` working, `NOTES.md` signed off by Erfan with the real raw-data format documented.
- `src/schemas.py` defines `ThreadRecord` (and the other core schemas) as the single source of truth — create/finalize this at the start of this phase per `CLAUDE.md`.

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
- [ ] ≥85% of raw threads survive to a valid `ThreadRecord` (if lower, investigate before proceeding).
- [ ] Every record validates against the Pydantic schema; zero PII regex hits in the final file.
- [ ] Unit tests: junk filter, PII sweep, section splitter, schema validation on fixture data.

## Human Sign-Off Gate
Erfan must review and approve `data/structured/pilot.jsonl` (50-thread pilot) before the full batch structuring run proceeds.

## Do Not Proceed
Do not start Phase 2 until `threads.jsonl` is finalized, the 85% survival threshold is met (or the shortfall investigated and accepted), and all acceptance criteria are checked.
