"""
Phase 4 — end-to-end generation CLI.

  python -m src.generation.pipeline --intake examples/intake_example.yaml --out out/
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.generation.generator import generate_resume
from src.generation.intake import load_intake
from src.generation.pagefit import fit_to_one_page


def run_pipeline(
    intake_path: Path,
    out_dir: Path,
    *,
    skip_pagefit: bool = False,
) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    intake = load_intake(intake_path)
    print(f"Loaded intake: {intake.name} → {intake.target_role}")

    print("Generating resume content (gpt-4o)…")
    result = generate_resume(intake)

    if skip_pagefit:
        from src.generation.renderer import render_and_compile, count_pdf_pages

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
        "pages": pages,
        "n_suggestions": len(result.suggestions),
    }
    print(
        f"Done: {pdf_path} ({pages} page{'s' if pages != 1 else ''}), "
        f"{len(result.suggestions)} suggestion(s)."
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Roastproof Phase 4 generation pipeline")
    parser.add_argument(
        "--intake",
        type=Path,
        required=True,
        help="Path to intake YAML/JSON",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("out"),
        help="Output directory (default: out/)",
    )
    parser.add_argument(
        "--skip-pagefit",
        action="store_true",
        help="Render once without the page-fit trim loop",
    )
    args = parser.parse_args(argv)
    try:
        run_pipeline(args.intake, args.out, skip_pagefit=args.skip_pagefit)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
