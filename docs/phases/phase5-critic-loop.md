# Phase 5 — Critic Loop & Suggestions Report

## Objective
Generate → critique → revise loop, plus a user-facing report.

## Prerequisites
- Phase 4 complete (including 4.5–4.8): generation produces a valid one-page PDF from an intake; fabrication test passes; elicitation sidecar + project eval + page-fill are in place.

## Already done in Phase 4 (do not rebuild)

Phase 5 **polishes** generated output. It does **not** replace these systems:

| Already exists | Where | Phase 5 role |
|---|---|---|
| Pre-gen elicitation + `*.qa.yaml` sidecar | `elicit.py`, `qa_store.py` | Critic may flag remaining weak bullets; do not invent a second Q&A store |
| Suggestions (`missing_skill`, `missing_metric`, `content_gap`, `project_evaluation`) | `suggestions.json`, norms gaps, `project_eval.py` | Report **merges** these with critic issues; do not invent a parallel suggestion pipeline |
| Corpus-grounded project portfolio eval | `project_eval.json` | Keep as input to the report; critic focuses on **bullet wording / rule violations** on the generated resume |
| Page-fill / tech lines | `pagefit.py`, template | Revision passes must stay G1-safe and one-page; re-run page-fit after revise if needed |
| Prompt library + fluff lint | `prompts.py`, `fluff.py` | Critic persona should align with hiring-reviewer + data-wins clauses |

## Tasks
1. **`critic.py`**
   - Synthesis-model call, distinct persona ("you are the review community" / hiring screen).
   - Inputs: rendered resume content JSON + rulebook + critiques retrieved *against the generated bullets themselves* (query the vector store with each generated bullet, dedupe).
   - Output: `[{section, issue, severity: high|med|low, suggested_fix}]`.
   - The critic must cite which rule or retrieved critique motivated each issue (`rule_id`/`critique_id`) — issues without grounding are dropped in post-processing (same grounding discipline as project eval: prefer real critique ids).
2. **Revision loop in `pipeline.py`**
   - Feed high+medium issues back to the generator for one revision pass; re-render; re-run critic once.
   - Stop after 2 iterations or when no high-severity issues remain.
   - Log a diff of bullets changed per round to `out/revision_log.json`.
   - Preserve / re-merge Phase 4 suggestion types after any regenerate (same pattern as project-eval re-merge after page-fit).
3. **`report.py`**
   - Markdown report for the user that **combines**:
     - critic issues found & fixed per round + remaining issues;
     - existing Phase 4 suggestions (skills/metrics/project_evaluation) with prevalence from `norms.json`;
     - elicitation/`status.json` summary (converged? pending questions?);
     - honest limitations (G1: gaps are suggestions, never silently invented).

## Acceptance Criteria
- [ ] Full loop runs on the example intake; revision demonstrably fixes ≥1 seeded weakness (test with an intake containing a deliberately weak, unquantified bullet).
- [ ] Every critic issue in output carries a grounding ID.
- [ ] Report renders clean Markdown; no fabricated numbers (all prevalence figures traced to `norms.json`).
- [ ] Report includes (does not drop) Phase 4 suggestion types and does not re-ask metrics already answered in the QA sidecar.

## Human Sign-Off Gate
None formally required for this phase itself, but the seeded-weakness demonstration should be shown to Erfan since it's the clearest proof the critic loop works.

## Do Not Proceed
Do not start Phase 6 until the revision loop demonstrably fixes a seeded weakness and every critic issue is grounded.
