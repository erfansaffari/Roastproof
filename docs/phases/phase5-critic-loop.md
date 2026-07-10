# Phase 5 — Critic Loop & Suggestions Report

## Objective
Generate → critique → revise loop, plus a user-facing report.

## Prerequisites
- Phase 4 complete: generation pipeline produces a valid one-page PDF from an intake, fabrication test passes.

## Tasks
1. **`critic.py`**
   - Sonnet call, distinct persona ("you are the review community").
   - Inputs: rendered resume content JSON + rulebook + critiques retrieved *against the generated bullets themselves* (query the vector store with each generated bullet, dedupe).
   - Output: `[{section, issue, severity: high|med|low, suggested_fix}]`.
   - The critic must cite which rule or retrieved critique motivated each issue (`rule_id`/`critique_id`) — issues without grounding are dropped in post-processing.
2. **Revision loop in `pipeline.py`**
   - Feed high+medium issues back to the generator for one revision pass; re-render; re-run critic once.
   - Stop after 2 iterations or when no high-severity issues remain.
   - Log a diff of bullets changed per round to `out/revision_log.json`.
3. **`report.py`**
   - Markdown report for the user: rules applied, issues found & fixed per round, remaining issues, missing-skill/metric suggestions with prevalence numbers (e.g. "Docker appears on 61% of SWE intern resumes in the community dataset — add it only if you actually know it"), and honest limitations.

## Acceptance Criteria
- [ ] Full loop runs on the example intake; revision demonstrably fixes ≥1 seeded weakness (test with an intake containing a deliberately weak, unquantified bullet).
- [ ] Every critic issue in output carries a grounding ID.
- [ ] Report renders clean Markdown; no fabricated numbers (all prevalence figures traced to `norms.json`).

## Human Sign-Off Gate
None formally required for this phase itself, but the seeded-weakness demonstration should be shown to Erfan since it's the clearest proof the critic loop works.

## Do Not Proceed
Do not start Phase 6 until the revision loop demonstrably fixes a seeded weakness and every critic issue is grounded.
