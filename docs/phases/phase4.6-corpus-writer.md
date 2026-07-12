# Phase 4.6 — Corpus-Derived Writer Knowledge (De-Hardcode)

## Objective
Stop hardcoding fluff lists and few-shots. Mine style + rewrite knowledge from the critique corpus, add a corpus-grounded project evaluator, tiered skill-gap report, and a professional hiring-reviewer prompt library for all LLM calls.

## Deliverables
1. **`src/generation/prompts.py`** — persona (big-tech 30s screen) + role profiles (SWE/ML/AI/FE/BE/…) + shared anatomy; data-wins clause; used by generator, elicit, project eval, miners, page-fit.
2. **`src/knowledge/style_mine.py`** → `data/knowledge/style_lexicon.json` (banned phrases + preferred patterns with thread ids).
3. **`src/knowledge/rewrite_mine.py`** → `data/knowledge/rewrite_examples.json` (before/critique/after pairs).
4. **`src/generation/fluff.py`** — loads lexicon; seed list is fallback only.
5. **`src/generation/project_eval.py`** — per-project verdicts + field gaps; ungrounded claims dropped; suggestions type `project_evaluation`.
6. **Tiered skill gaps** — core (>50%) and common (25–50%) with bucket disclosure.

## Commands
```bash
# One-time mining (G3 --yes)
python -m src.knowledge.style_mine --yes
python -m src.knowledge.rewrite_mine --yes

# Generate with project eval
python -m src.generation.pipeline --intake examples/my_intake.yaml --out out/mine46
```

## Acceptance
- [x] Prompt invariants tested (persona + data-wins + JSON contract).
- [x] Lexicon + rewrite artifacts mined from corpus.
- [x] Fluff lint uses lexicon without banning generic nouns like "development".
- [x] Project eval suggestions cite evidence ids.
- [x] Skill suggestions tiered core/common.
- [x] Unit tests green; live run on `my_intake.yaml`.

## Honesty
Corpus is swe_intern-heavy. Thin roles fall back to SWE-family norms/retrieval with an explicit disclosure in the norms block and skill suggestions.
