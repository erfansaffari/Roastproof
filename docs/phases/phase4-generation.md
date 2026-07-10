# Phase 4 — Generation Pipeline

## Objective
End-to-end: user info → `ResumeContent` JSON → LaTeX → compiled one-page PDF.

## Prerequisites
- Phase 3 complete: rulebook (`rulebook.json`) and vector store (`critiques_v1`) built and QA-approved.
- `data/norms/norms.json` available from Phase 2.

## Tasks
1. **`intake.py`**
   - v1 input = a single YAML/JSON file the user fills (`examples/intake_example.yaml`): raw free-text descriptions of education, each experience, each project, self-reported skills, target role, year.
   - Loader validates into an `Intake` Pydantic model.
2. **`generator.py`** — one Sonnet call assembling:
   - System prompt: role ("expert CS resume writer grounded in community review data"), guardrail G1 verbatim, output schema, bullet constraints.
   - Context blocks:
     (a) applicable rulebook rules (filtered by role/profile, capped at 20, ordered by frequency);
     (b) retrieved critiques per section via `retrieve()` (k=5 per section);
     (c) norms block — skill prevalence for the target role, with instruction: *if a high-prevalence skill (>50%) is absent from the user's skills, DO NOT add it; append it to `suggestions` output instead.*
   - Output: `{"resume": ResumeContent, "suggestions": [{"type": "missing_skill|missing_metric|content_gap", "detail": str}]}` — validated, one retry.
3. **`renderer.py`**
   - `templates/jakes_resume.tex.j2` faithful to the Jake's Resume layout.
   - `latex_escape()` handling `% & # _ $ { } ~ ^ \` (unit-tested exhaustively).
   - Compile via Tectonic subprocess; capture stderr; on failure, save the `.tex` for debugging and raise a clear error. Renderer contains **no LLM calls**.
4. **`pagefit.py`**
   - Compile → count pages (PyMuPDF). If >1 page: call generator with a trim instruction ("cut lowest-value bullets, target N fewer lines") and re-render.
   - Max 3 attempts, then hard-trim deterministically (drop last project bullets) and warn.
5. **`pipeline.py`**
   - CLI: `python -m src.generation.pipeline --intake examples/intake_example.yaml --out out/`.
   - Produces `resume.pdf`, `resume.tex`, `content.json`, `suggestions.json`.

## Acceptance Criteria
- [ ] Example intake compiles to a clean one-page PDF that visually matches the template.
- [ ] **Fabrication test:** run with an intake that omits Git while norms show Git >50% — Git must appear in suggestions, NOT in the resume. Add this as an automated test (mock LLM optional; also run live once).
- [ ] `latex_escape` unit tests pass including adversarial strings (`"C# & F_measure 100%"`).
- [ ] Page-fit loop demonstrated on a deliberately overstuffed intake.

## Human Sign-Off Gate
None formally required; but the fabrication test result should be shared with Erfan since G1 is a hard guardrail.

## Do Not Proceed
Do not start Phase 5 until the example intake produces a clean one-page PDF and the fabrication test passes.
