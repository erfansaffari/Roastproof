# Phase 4.5 — Writer Quality (not just formatting)

## Objective
Make generation a **community-grounded rewrite**, not a LaTeX formatter. Fix skills-gap bugs, enforce anti-fluff, and elicit missing metrics *before* generation.

## Diagnosis (verified from `logs/llm_calls.jsonl`)
Context injection was **not** broken: Phase 4 prompts already contained rules (~1.7k chars), retrieved critiques (~2.9k), and norms. The model still emitted fluff because:
1. G1 was framed as "change nothing" → copy-paste wording.
2. No few-shot before→after rewrites.
3. Skills gap compared raw `"Git"` ≠ `"Git/GitHub Actions CI"`.
4. No deterministic fluff lint.

## Tasks
1. **Prompt restructure** — facts-frozen / wording-mandatory; few-shots; annotated bullets `{text, rewritten_from, gaps}`.
2. **Skills-gap fix** — expand compound skills; diff against final normalized owned set; cite prevalence %; never suggest PRESENT skills; discard LLM "breadth" padding suggestions.
3. **Banned-fluff lint** — `src/generation/fluff.py`; one generator retry on violation; pytest.
4. **Elicitation pass** — `elicit.py` (gpt-4o-mini) → `questions.json`; intake `answers:` map for second run.

## CLI
```bash
# Questions only
python -m src.generation.pipeline --intake examples/my_intake.yaml --out out/mine --elicit-only

# Full (elicit + generate). Add answers: to YAML and re-run for metrics.
python -m src.generation.pipeline --intake examples/my_intake.yaml --out out/mine

# Skip elicit
python -m src.generation.pipeline --intake examples/my_intake.yaml --out out/mine --skip-elicit
```

## Acceptance
- [x] Log check documented; injection present.
- [x] Compound Git not suggested when `Git/GitHub Actions CI` owned.
- [x] Fluff lint + unit tests.
- [x] Annotated bullets + gap→`missing_metric` suggestions.
- [x] Elicitation → `questions.json` + `answers:` support.

## Do Not Proceed
Phase 5 critic loop still next — it should polish rewritten output, not rescue formatter output.
