# Phase 6 — Evaluation

## Objective
Quantified evidence that the knowledge base helps. Headline number: critique-coverage vs. baseline.

## Prerequisites
- Phase 5 complete: full generate→critique→revise pipeline working end to end.
- `data/holdout/` frozen since Phase 0, untouched by any code outside `src/eval/` (G4).

## Tasks
1. **`harness.py`**
   - For each of the 100 holdout threads: reconstruct an Intake from the original resume content (Haiku extraction pass — this is the only holdout-touching code, lives in `src/eval/`), run a system variant, collect the generated `ResumeContent`.
2. **`judge.py`**
   - LLM judge (Sonnet) rubric: given the holdout thread's real critiques and the generated resume, score per-critique whether the generated resume avoids/fixes that issue (0/0.5/1). Coverage = mean.
   - Also a 1–10 holistic quality score.
   - Judge prompt is variant-blind (never told which system produced the resume).
   - Run judge with temperature 0; each resume judged once (stretch: 3× and average).
3. **`ablations.py`** — three variants, same intakes, same judge:
   - (A) bare Sonnet prompt, no knowledge
   - (B) + rulebook
   - (C) + rulebook + RAG + norms (full system, no critic loop)
   - optional (D) full system + critic loop
4. **`report.py` (eval)**
   - Table of coverage/quality per variant with 95% bootstrap CIs; per-category coverage breakdown; 5 qualitative before/after examples.
   - Output `eval/RESULTS.md`.
5. **Human ground truth (Erfan, manual)**
   - Post 3–5 generated resumes to the Discord channel; record real feedback in `eval/HUMAN_FEEDBACK.md`.

## Cost Note
100 intakes × 4 variants × (generation + judging) ≈ 800–1000 Sonnet calls. Print estimate, require `--yes` gate; support `--subset 20` for cheap iteration.

## Acceptance Criteria
- [ ] All variants evaluated on the same 100 intakes; results reproducible from one command.
- [ ] `RESULTS.md` written with CIs and examples.
- [ ] Holdout-isolation check passes (G4).

## Human Sign-Off Gate
Erfan manually posts 3–5 generated resumes to the Discord channel and records feedback in `eval/HUMAN_FEEDBACK.md`. Do not skip this — it's the only real-world validation in the project.

## Do Not Proceed
Do not start Phase 7 until `RESULTS.md` is complete, the G4 holdout-isolation check passes, and human Discord feedback is recorded.
