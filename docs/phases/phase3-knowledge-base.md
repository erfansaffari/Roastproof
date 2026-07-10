# Phase 3 — Knowledge Base (Rulebook + Vector Store)

## Objective
Produce `rulebook.json` (30–60 consensus rules) and a queryable ChromaDB critique store with a clean retrieval API.

## Prerequisites
- Phase 2 complete: `norms.json` and `FINDINGS.md` exist.
- `data/structured/threads.jsonl` is the finalized corpus from Phase 1.

## Tasks
1. **`rulebook.py`**
   - Pass 1 (map): batch threads in groups of 50; Sonnet extracts candidate rules per batch with supporting thread_ids.
   - Pass 2 (reduce): Sonnet merges/dedupes candidates across batches into canonical Rules, summing frequency, keeping 2–3 best evidence examples each.
   - Threshold: keep rules with frequency ≥ 10. Output validated `Rule` objects.
   - Deterministic post-check in Python: recount frequency by matching supporting thread_ids against the corpus; discard rules whose claimed evidence doesn't exist (hallucination guard).
2. **`vectorstore.py`**
   - Explode threads → critique points (expect 5–15K). Skip critiques with empty `issue`.
   - Embed composite string: `"[{target_role}] [{profile summary}] [{section_targeted}] resume text: {original_text} || critique: {issue} → {suggestion}"`.
   - Metadata per point: role, section, year, has_internships, agreement_signal, thread_id.
   - Persistent Chroma collection `critiques_v1`. Idempotent build (`--rebuild` flag to wipe).
3. **`retrieve.py`**
   - `retrieve(profile: ApplicantProfile, section: str, query_text: str, k=8) -> list[CritiquePoint]`
   - Chroma query with metadata filter on role (exact) and section; over-fetch k×3, then re-rank: `score = 0.7·similarity + 0.2·profile_match(year, internships) + 0.1·normalized(agreement_signal)`.
   - Returns typed objects + a `format_for_prompt()` helper producing a compact numbered block.
4. **Retrieval QA** — `scripts/test_retrieval.py` with 10 canned queries; prints top-5 for each. Erfan manually grades relevance; iterate on composite string/weights until ≥8/10 queries look right. Record the grading in `NOTES.md`.

## Acceptance Criteria
- [ ] 30–60 rules, all passing the deterministic evidence check.
- [ ] Vector store built; retrieval QA graded ≥8/10 by Erfan.
- [ ] Unit tests: composite string builder, re-ranker math, evidence checker.

## Human Sign-Off Gate
Erfan must manually grade the 10 canned retrieval queries and confirm ≥8/10 look right before Phase 4 generation begins (generation depends on retrieval quality).

## Do Not Proceed
Do not start Phase 4 until the rulebook passes its evidence check and retrieval QA is signed off at ≥8/10.
