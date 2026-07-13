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
- [x] Full loop runs on the example intake; revision demonstrably fixes ≥1 seeded weakness (`examples/seeded_weak_intake.yaml`; live `out/critic_demo` rewrote the seeded weak bullets over 2 rounds; mocked `test_critic_loop_fixes_seeded_weakness`).
- [x] Every critic issue in output carries a grounding ID (`drop_ungrounded_issues` keeps only `rule_id`/`critique_id`; verified 0 ungrounded in `out/critic_demo/critic.json`).
- [x] Report renders clean Markdown; no fabricated numbers (prevalence word-boundary-matched to `norms.json`; verified no hard numbers on demo bullets).
- [x] Report includes (does not drop) Phase 4 suggestion types and does not re-ask metrics already answered in the QA sidecar (report merges `suggestions.json` + surfaces pending sidecar questions; `test_report_renders_clean_markdown`).

### Implementation notes
- Revision uses a **targeted per-bullet rewrite** (`revise_bullets`): only flagged bullets are rewritten and spliced in place; all other bullets stay byte-identical, so the one-page layout is preserved (only a page re-check + deterministic hard-trim fallback).
- Loop entry = high OR medium issues; stop at 2 rounds or when a round produces no revisable diffs. Artifacts: `revision_log.json`, `critic.json`, `report.md`; critic fields added to `status.json`.
- G1 guard: reviewer `suggested_fix` text is never embedded into a bullet (prompt + deterministic `_has_instruction_leak` reject).

### Two-stage pipeline (2026-07-13 refactor)
The pipeline is organized around one boundary — *does the generated resume exist yet?*
- **Stage A — Input review (pre-generation):** elicitation questions only (`elicit.py`, `*.qa.yaml`). Purpose: extract facts from the user.
- **Stage B — Output review (post-generation):** everything that judges the finished resume runs on the generated `ResumeContent`, in order: critic → revise loop → **project-eval (now on the generated resume, not raw intake)** → skill-gap surfacing. All of it lands in one `report.md` with `## Stage A` / `## Stage B` sections.

Consequences:
- **Bullet metric/scope weaknesses are the critic's job, not Phase 4 suggestions.** `enforce_g1` no longer emits `missing_metric`/`content_gap`; a deterministic `critic.bullet_gap_hints(resume)` feeds `run_critic(..., gap_hints=…)` so each weak bullet is reported once, grounded. `suggestions.json` carries only `missing_skill` + `project_evaluation`.
- `evaluate_projects(intake, resume, …)` consumes generated project bullets; retrieval query is built from them. Verdicts shift once on the first post-refactor run (expected).

## Human Sign-Off Gate
None formally required for this phase itself, but the seeded-weakness demonstration should be shown to Erfan since it's the clearest proof the critic loop works.

## Do Not Proceed
Do not start Phase 6 until the revision loop demonstrably fixes a seeded weakness and every critic issue is grounded.
