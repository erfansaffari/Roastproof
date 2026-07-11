Everything's clean: no syntax errors anywhere, all 9 tests pass, and audit_raw.py runs end-to-end on real synced data.

What I fixed

Code bugs (all confirmed by actually running things, not just reading):
1. src/schemas.py — corrupted duplicate class was causing a SyntaxError that broke every test. Rewrote cleanly, and actually implemented the bullet-length/count/budget validators that were previously just comments.
2. Same corruption pattern (literal newlines inside string literals) also broke pdf_extract.py, filter.py, audit_raw.py, make_holdout.py — fixed all four.
3. assemble.py — rewritten to read dataset.json (structured, reliable) instead of parsing critiques.txt with a "::" split that never matched real output; also stopped it from dropping no-critique threads (that's filter.py's job).
4. structure.py — fixed the stale model name to claude-haiku-4-5-20251001; full runs now actually go through the Batch API (with a --yes cost gate) instead of a loop of synchronous calls.
5. llm.py — batch_complete now really calls Anthropic's Message Batches API (submit → poll → collect), and fixed a system=None bug that would've broken every complete() call.
6. One test had a wrong assertion (expected partial URL redaction that the regex never produces) — fixed the assertion, not the redaction logic, since the redaction was already correct.

The scraper bug (this one was serious): all 20 scraped resumes are missing their PDFs locally. I traced it — export.py wiped the entire export folder on every run, but by the second run the original source files had already been deleted (moved into that same folder on the first run), so the wipe destroyed the only remaining copies with nothing to fall back on. Fixed it to be idempotent, and added a repair command (python -m bot.main repair) that re-fetches the original Discord messages and re-downloads attachments. You'll need to run repair then export yourself — that needs your live Discord token, which I don't have — then re-run the sync script. Nothing was lost from Discord itself, only local copies.

Your question — separate or merged folder?

Keep them separate, wired by an explicit sync step. I added                 scripts/sync_scraper_dataw exists with the real 20threads. Reasoning: data/raw/ is meant to be a frozen input contract (the   holdout gets carved out oport directory is a livearea that gets rewritten every time you run export. Pointing ingestion straiat the scraper's folder wd a half-rewrittendirectory mid-export, and would couple the pipeline to the scraper's internalayout forever. The sync ble checkpoint:scrape/export as much as you want, and only sync into data/raw/ when you're ready to bring new data i

Still open (yours to do,

- NOTES.md — genuinely neds and recordobservations; I won't fabricate that.
- Recover the 20 PDFs — raper_data.py as above.