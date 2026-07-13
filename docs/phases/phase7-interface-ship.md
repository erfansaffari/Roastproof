# Phase 7 — Interface & Ship

## Objective
Usable demo + portfolio-grade documentation.

## Prerequisites
- Phase 6 complete: `RESULTS.md` with real eval numbers, human Discord feedback recorded.

## Tasks
1. **`streamlit_app.py`**
   - Form mirroring the Intake schema (dynamic add-experience/add-project rows).
   - **Elicitation step (Phase 4.7):** after the first pass, show pending questions from the QA sidecar (`*.qa.yaml` / `questions.json`); let the user answer or skip in the UI; re-run generation with answers applied. Do not force users to edit YAML by hand in the demo.
   - Generate button with progress states (retrieving → eliciting → writing → rendering → reviewing / critic).
   - PDF preview + download; show page-fill / status when available (`status.json`).
   - Suggestions + Phase 5 report rendered inline (skills/metrics/project_evaluation + critic issues).
   - Expander showing which rules/critiques were retrieved (transparency = demo wow-factor).
2. **README.md**
   - Architecture diagram (Mermaid), quickstart, eval headline numbers, 3 before/after examples, limitations, ethics note (no-fabrication policy, data anonymization). Keep aligned with `CLAUDE.md` phase status and OpenAI stack (G3).
3. **v2 backlog** (documented, not built):
   - Fine-tune Llama 3.1 8B (Unsloth) on weak→improved bullet pairs mined from `logs/` + critiques, evaluated with the Phase 6 harness.
   - Conversational intake interviewer (deeper than the current sidecar Q&A step).
   - Multi-template support.
   - ATS-parse check.

## Acceptance Criteria
- [ ] `streamlit run src/app/streamlit_app.py` → full flow works locally end-to-end.
- [ ] README complete with real eval numbers.

## Human Sign-Off Gate
None formally required — this is the final ship phase. Erfan does a final walkthrough of the Streamlit demo before considering v1 done.

## Do Not Proceed
This is the last phase. Project v1 is complete once both acceptance criteria are checked; v2 backlog items are explicitly out of scope until then.
