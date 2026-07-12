"""
Phase 4 / 4.5 / 4.6 — end-to-end generation CLI.

  python -m src.generation.pipeline --intake examples/my_intake.yaml --out out/mine
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.generation.elicit import elicit_questions, write_questions
from src.generation.generator import generate_resume
from src.generation.intake import load_intake
from src.generation.pagefit import fit_to_one_page
from src.generation.project_eval import evaluate_projects, project_eval_to_suggestions


def run_pipeline(
    intake_path: Path,
    out_dir: Path,
    *,
    skip_pagefit: bool = False,
    skip_elicit: bool = False,
    elicit_only: bool = False,
    skip_project_eval: bool = False,
) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    intake = load_intake(intake_path)
    print(f"Loaded intake: {intake.name} → {intake.target_role}")

    questions_path = out_dir / "questions.json"
    if not skip_elicit:
        print("Elicitation pass (gpt-4o-mini)…")
        elicit = elicit_questions(intake)
        write_questions(elicit, questions_path)
        unanswered = [q for q in elicit.questions if q.id not in (intake.answers or {})]
        print(
            f"  wrote {questions_path} ({len(elicit.questions)} question(s), "
            f"{len(unanswered)} unanswered)."
        )
        if elicit_only:
            return {
                "questions": str(questions_path),
                "n_questions": len(elicit.questions),
                "n_unanswered": len(unanswered),
            }
        if unanswered and not intake.answers:
            print(
                "  Tip: fill `answers:` in your intake YAML (map of question id → answer) "
                "and re-run for stronger metrics."
            )
    elif elicit_only:
        print("ERROR: --elicit-only requires elicitation (omit --skip-elicit).", file=sys.stderr)
        raise SystemExit(2)

    print("Generating resume content (gpt-4o)…")
    result = generate_resume(intake)

    if not skip_project_eval and intake.projects:
        print("Project evaluation (gpt-4o)…")
        peval = evaluate_projects(intake)
        peval_path = out_dir / "project_eval.json"
        peval_path.write_text(peval.model_dump_json(indent=2), encoding="utf-8")
        extra = project_eval_to_suggestions(peval)
        # Dedupe against existing
        existing = {(s.type, s.detail) for s in result.suggestions}
        for s in extra:
            if (s.type, s.detail) not in existing:
                result.suggestions.append(s)
        print(f"  wrote {peval_path} (+{len(extra)} project suggestion(s)).")

    if skip_pagefit:
        from src.generation.renderer import count_pdf_pages, render_and_compile

        tex_path, pdf_path = render_and_compile(result.resume, out_dir)
        pages = count_pdf_pages(pdf_path)
    else:
        print("Rendering + page-fit…")
        result, tex_path, pdf_path, pages = fit_to_one_page(
            intake,
            result,
            out_dir,
            generate_fn=generate_resume,
        )

    content_path = out_dir / "content.json"
    suggestions_path = out_dir / "suggestions.json"
    content_path.write_text(
        result.resume.model_dump_json(indent=2),
        encoding="utf-8",
    )
    suggestions_path.write_text(
        json.dumps([s.model_dump() for s in result.suggestions], indent=2),
        encoding="utf-8",
    )

    summary = {
        "pdf": str(pdf_path),
        "tex": str(tex_path),
        "content": str(content_path),
        "suggestions": str(suggestions_path),
        "questions": str(questions_path) if questions_path.exists() else None,
        "pages": pages,
        "n_suggestions": len(result.suggestions),
    }
    print(
        f"Done: {pdf_path} ({pages} page{'s' if pages != 1 else ''}), "
        f"{len(result.suggestions)} suggestion(s)."
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Roastproof Phase 4/4.5/4.6 generation pipeline"
    )
    parser.add_argument("--intake", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("out"))
    parser.add_argument("--skip-pagefit", action="store_true")
    parser.add_argument("--skip-elicit", action="store_true")
    parser.add_argument("--elicit-only", action="store_true")
    parser.add_argument(
        "--skip-project-eval",
        action="store_true",
        help="Skip corpus-grounded project portfolio evaluation",
    )
    args = parser.parse_args(argv)
    try:
        run_pipeline(
            args.intake,
            args.out,
            skip_pagefit=args.skip_pagefit,
            skip_elicit=args.skip_elicit,
            elicit_only=args.elicit_only,
            skip_project_eval=args.skip_project_eval,
        )
    except SystemExit:
        raise
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
