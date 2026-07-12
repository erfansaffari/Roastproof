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

## Phase 4.7 follow-on — Elicitation memory & convergence
Fixes infinite/shifting questions and flipping project field gaps:

1. **`src/generation/qa_store.py`** — sidecar `*.qa.yaml` next to intake; stable content-hash ids; statuses `pending|answered|declined`; legacy `intake.answers` merged once.
2. **Elicitation** — full prior Q&A history in the prompt; `impact` + `complete` fields; MiniLM semantic dedup; round budget (`--max-elicit-rounds`, default 3); rounds 2+ only admit `high` impact.
3. **Generator** — answers block is full Q→A pairs; declined topics suppress repeat `missing_metric` suggestions.
4. **Project eval** — prior-eval memory, `portfolio_composition`, verbatim `evidence_quote` validation for field gaps; `temperature=0`.
5. **`status.json`** — round / converged / pending / eval_changed printed every run.

```bash
# First run seeds examples/my_intake.qa.yaml
python -m src.generation.pipeline --intake examples/my_intake.yaml --out out/mine47 --elicit-only
# Edit answer: fields in the sidecar (or set to 'skip'), then:
python -m src.generation.pipeline --intake examples/my_intake.yaml --out out/mine48
```


## Phase 4.8 follow-on — Bidirectional page-fill
Community resumes fill one page; thin drafts now expand instead of only trimming:

1. **`measure_page_fill`** in `renderer.py` — PyMuPDF content bbox / usable height.
2. **Norms** — `bullets_per_entry_p75`, `total_bullets_median/p75` per bucket; generator targets the upper band when intake material allows (schema ceilings raised to 5/4/26 as safety caps).
3. **`fit_to_one_page`** — trim if >1 page; if 1 page but fill < `--fill-target` (default 0.85), expand using unused intake facts (G1-safe); keep best draft.
4. **`expand_content` elicitation** — when still under-filled, append questions to the QA sidecar even if metric-converged.
5. **`status.json`** — `fill_ratio`, `fill_target`, `expand_attempts`, `expansion_questions_added`.

### Page-fill density (tech lines + QA coverage)
Under-fill on thin intakes was often layout density, not missing metrics:
- Experience and projects both render a dedicated `\textit{\small ...}` **technologies line** under the title/date row (not inline with the name). Experience `technologies` is extracted only from that entry's intake description / related QA (G1); corpus norms never invent tools.
- `unused_intake_facts` uses a stricter overlap threshold (0.75) for answered QA so a partially-related bullet does not swallow a distinct elicited fact; facts are labeled with `relates_to` so expand attaches them to the right entry.

```bash
python -m src.generation.pipeline --intake examples/my_intake.yaml --out out/mine49 --fill-target 0.85
```

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
- [x] QA sidecar + semantic dedup + convergence stopping rule.
- [x] Field-gap quote validation + prior-eval stability.
- [x] Page-fill measurement + expand loop + expand_content elicitation.
- [x] Dedicated technologies lines for experience + projects; QA unused-fact coverage fix.
- [x] Live arya intake fill improved ~0.77 → ~0.85 with tech lines + recovered QA facts.

## Honesty
Corpus is swe_intern-heavy. Thin roles fall back to SWE-family norms/retrieval with an explicit disclosure in the norms block and skill suggestions.
