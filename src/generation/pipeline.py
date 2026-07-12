"""
Phase 4 / 4.5 / 4.6 / 4.7 — end-to-end generation CLI.

  python -m src.generation.pipeline --intake examples/my_intake.yaml --out out/mine

Elicitation Q&A lives in a sidecar next to the intake:
  examples/my_intake.yaml → examples/my_intake.qa.yaml
Edit `answer:` in place, then re-run. Set answer to 'skip' to decline.
"""

from __future__ import annotations

import argparse
import json
import sys
from functools import partial
from pathlib import Path

from src.generation.elicit import elicit_questions, write_questions
from src.generation.generator import generate_resume
from src.generation.intake import load_intake
from src.generation.pagefit import fit_to_one_page
from src.generation.project_eval import (
    eval_changed,
    evaluate_projects,
    project_eval_to_suggestions,
)
from src.generation.qa_store import (
    DEFAULT_MAX_ROUNDS,
    counts,
    load_qa_store,
    merge_legacy_answers,
    save_qa_store,
    sidecar_path,
)
from src.schemas import ProjectEvalResult


def run_pipeline(
    intake_path: Path,
    out_dir: Path,
    *,
    skip_pagefit: bool = False,
    skip_elicit: bool = False,
    elicit_only: bool = False,
    skip_project_eval: bool = False,
    max_elicit_rounds: int = DEFAULT_MAX_ROUNDS,
    prev_eval_path: Path | None = None,
) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    intake_path = Path(intake_path)
    intake = load_intake(intake_path)
    print(f"Loaded intake: {intake.name} → {intake.target_role}")

    qa_path = sidecar_path(intake_path)
    qa_store = load_qa_store(qa_path)
    qa_store = merge_legacy_answers(qa_store, intake)
    if intake.answers:
        print(
            f"  merged {len(intake.answers)} legacy intake.answers into sidecar "
            f"({qa_path.name}). Prefer editing the sidecar going forward."
        )

    questions_path = out_dir / "questions.json"
    elicit_meta: dict = {
        "round": qa_store.round,
        "converged": qa_store.converged,
        "surviving_count": 0,
        "stop_reason": "",
        "counts": counts(qa_store),
    }

    if not skip_elicit:
        print("Elicitation pass (gpt-4o-mini, temp=0)…")
        _raw, qa_store, elicit_meta = elicit_questions(
            intake,
            qa_store,
            max_rounds=max_elicit_rounds,
        )
        save_qa_store(qa_store, qa_path)
        write_questions(qa_store, questions_path)
        c = elicit_meta["counts"]
        print(
            f"  sidecar {qa_path} — round={elicit_meta['round']}, "
            f"new_surviving={elicit_meta['surviving_count']}, "
            f"pending={c['pending']} answered={c['answered']} declined={c['declined']}, "
            f"converged={qa_store.converged}"
        )
        if qa_store.converged:
            reason = elicit_meta.get("stop_reason") or elicit_meta.get("completion_reason") or ""
            print(
                f"  Elicitation complete ({reason}). "
                "Resume is as strong as the provided facts allow."
            )
        elif c["pending"]:
            print(
                f"  Tip: fill `answer:` for {c['pending']} pending question(s) in "
                f"{qa_path}, then re-run."
            )
        if elicit_only:
            status = {
                "round": qa_store.round,
                "converged": qa_store.converged,
                "pending_questions": c["pending"],
                "answered": c["answered"],
                "declined": c["declined"],
                "sidecar": str(qa_path),
                "stop_reason": elicit_meta.get("stop_reason") or "",
            }
            (out_dir / "status.json").write_text(
                json.dumps(status, indent=2), encoding="utf-8"
            )
            return {
                "questions": str(questions_path),
                "sidecar": str(qa_path),
                "n_questions": c["total"],
                "n_unanswered": c["pending"],
                "converged": qa_store.converged,
                "status": status,
            }
    elif elicit_only:
        print("ERROR: --elicit-only requires elicitation (omit --skip-elicit).", file=sys.stderr)
        raise SystemExit(2)
    else:
        # Still persist any legacy-answer merge
        save_qa_store(qa_store, qa_path)

    print("Generating resume content (gpt-4o)…")
    gen_fn = partial(generate_resume, qa_store=qa_store)
    result = gen_fn(intake)

    eval_changed_flag = False
    peval_path = out_dir / "project_eval.json"
    if not skip_project_eval and intake.projects:
        print("Project evaluation (gpt-4o, temp=0)…")
        prior_path = prev_eval_path or peval_path
        prior: ProjectEvalResult | None = None
        if prior_path.exists():
            try:
                prior = ProjectEvalResult.model_validate_json(
                    prior_path.read_text(encoding="utf-8")
                )
            except Exception:
                prior = None
        peval = evaluate_projects(intake, prior_eval=prior)
        eval_changed_flag = eval_changed(prior, peval)
        peval_path.write_text(peval.model_dump_json(indent=2), encoding="utf-8")
        extra = project_eval_to_suggestions(peval)
        existing = {(s.type, s.detail) for s in result.suggestions}
        for s in extra:
            if (s.type, s.detail) not in existing:
                result.suggestions.append(s)
        print(
            f"  wrote {peval_path} (+{len(extra)} project suggestion(s); "
            f"changed_since_prev={eval_changed_flag})."
        )

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
            generate_fn=gen_fn,
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

    c = counts(qa_store)
    status = {
        "round": qa_store.round,
        "converged": qa_store.converged,
        "pending_questions": c["pending"],
        "answered": c["answered"],
        "declined": c["declined"],
        "eval_changed_since_prev": eval_changed_flag,
        "sidecar": str(qa_path),
        "stop_reason": elicit_meta.get("stop_reason") or "",
    }
    status_path = out_dir / "status.json"
    status_path.write_text(json.dumps(status, indent=2), encoding="utf-8")

    if qa_store.converged:
        verdict = (
            f"Status: CONVERGED (round {qa_store.round}) — "
            "no further elicitation needed; resume is as strong as facts allow."
        )
    elif c["pending"]:
        verdict = (
            f"Status: {c['pending']} pending question(s) in {qa_path.name} — "
            "answer them and re-run for a stronger resume."
        )
    else:
        verdict = f"Status: round {qa_store.round}, not yet converged."
    print(verdict)

    summary = {
        "pdf": str(pdf_path),
        "tex": str(tex_path),
        "content": str(content_path),
        "suggestions": str(suggestions_path),
        "questions": str(questions_path) if questions_path.exists() else None,
        "sidecar": str(qa_path),
        "status": str(status_path),
        "pages": pages,
        "n_suggestions": len(result.suggestions),
        "converged": qa_store.converged,
    }
    print(
        f"Done: {pdf_path} ({pages} page{'s' if pages != 1 else ''}), "
        f"{len(result.suggestions)} suggestion(s)."
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Roastproof Phase 4/4.5/4.6/4.7 generation pipeline"
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
    parser.add_argument(
        "--max-elicit-rounds",
        type=int,
        default=DEFAULT_MAX_ROUNDS,
        help="Stop elicitation after this many rounds (default 3)",
    )
    parser.add_argument(
        "--prev-eval",
        type=Path,
        default=None,
        help="Prior project_eval.json for stability (default: out/project_eval.json)",
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
            max_elicit_rounds=args.max_elicit_rounds,
            prev_eval_path=args.prev_eval,
        )
    except SystemExit:
        raise
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
