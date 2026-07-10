# Phase 7 — Interface & Ship

## Objective
Usable demo + portfolio-grade documentation.

## Prerequisites
- Phase 6 complete: `RESULTS.md` with real eval numbers, human Discord feedback recorded.

## Tasks
1. **`streamlit_app.py`**
   - Form mirroring the Intake schema (dynamic add-experience/add-project rows).
   - Generate button with progress states (retrieving → writing → rendering → reviewing).
   - PDF preview + download.
   - Suggestions report rendered inline.
   - Expander showing which rules/critiques were retrieved (transparency = demo wow-factor).
2. **README.md**
   - Architecture diagram (Mermaid), quickstart, eval headline numbers, 3 before/after examples, limitations, ethics note (no-fabrication policy, data anonymization).
3. **v2 backlog** (documented, not built):
   - Fine-tune Llama 3.1 8B (Unsloth) on weak→improved bullet pairs mined from `logs/` + critiques, evaluated with the Phase 6 harness.
   - Conversational intake interviewer.
   - Multi-template support.
   - ATS-parse check.

## Acceptance Criteria
- [ ] `streamlit run src/app/streamlit_app.py` → full flow works locally end-to-end.
- [ ] README complete with real eval numbers.

## Human Sign-Off Gate
None formally required — this is the final ship phase. Erfan does a final walkthrough of the Streamlit demo before considering v1 done.

## Do Not Proceed
This is the last phase. Project v1 is complete once both acceptance criteria are checked; v2 backlog items are explicitly out of scope until then.
