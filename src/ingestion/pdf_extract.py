import argparse
import json
import os
import shutil
from collections import Counter
from pathlib import Path

import fitz  # PyMuPDF
import numpy as np
import pdfplumber

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
RESUME_EXTENSIONS = {".pdf"} | IMAGE_EXTENSIONS


def tesseract_available() -> bool:
    """True if the tesseract binary is on PATH (required for PyMuPDF OCR)."""
    if shutil.which("tesseract"):
        return True
    # Homebrew Apple Silicon default
    brew_tess = Path("/opt/homebrew/bin/tesseract")
    if brew_tess.exists():
        os.environ["PATH"] = f"/opt/homebrew/bin:{os.environ.get('PATH', '')}"
        return True
    return False


def ensure_tessdata_prefix() -> None:
    """Set TESSDATA_PREFIX if unset and a Homebrew tessdata dir exists."""
    if os.environ.get("TESSDATA_PREFIX"):
        return
    for candidate in (
        Path("/opt/homebrew/share/tessdata"),
        Path("/usr/local/share/tessdata"),
    ):
        if candidate.is_dir():
            os.environ["TESSDATA_PREFIX"] = str(candidate)
            return


def _open_as_pdf(path: Path) -> fitz.Document:
    """
    Open a resume file as a PDF document.

    Discord attachments are often screenshots (png/jpg). PyMuPDF can convert
    those to a single-page PDF so the rest of the extraction path is uniform.
    """
    suffix = path.suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        img_doc = fitz.open(path)
        try:
            pdf_bytes = img_doc.convert_to_pdf()
        finally:
            img_doc.close()
        return fitz.open("pdf", pdf_bytes)
    return fitz.open(path)


def extract_text_pymupdf(pdf_path: Path) -> str:
    """Extracts text from a PDF (or image-converted-to-PDF) using PyMuPDF."""
    try:
        with _open_as_pdf(pdf_path) as doc:
            text = "".join(page.get_text() for page in doc)
            # Image resumes have no embedded text layer — try OCR when available.
            if not text.strip():
                if not tesseract_available():
                    return ""
                ensure_tessdata_prefix()
                ocr_parts: list[str] = []
                for page in doc:
                    try:
                        tp = page.get_textpage_ocr(dpi=300, full=True)
                        ocr_parts.append(page.get_text(textpage=tp))
                    except Exception as e:
                        print(f"OCR failed for {pdf_path.name}: {e}")
                        break
                text = "".join(ocr_parts)
        return text
    except Exception as e:
        print(f"Error processing {pdf_path} with PyMuPDF: {e}")
        return ""


def extract_text_pdfplumber(pdf_path: Path) -> str:
    """Extracts text from a PDF using pdfplumber. Images are skipped (not supported)."""
    if pdf_path.suffix.lower() in IMAGE_EXTENSIONS:
        return ""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            text = "".join(page.extract_text() for page in pdf.pages if page.extract_text())
        return text
    except Exception as e:
        print(f"Error processing {pdf_path} with pdfplumber: {e}")
        return ""


def score_extraction_quality(text: str) -> float:
    """
    Calculates a heuristic quality score for the extracted text.
    The score is the fraction of lines that seem like valid sentences or bullet points.
    A higher score is better.
    """
    if not text:
        return 0.0

    lines = text.split("\n")
    valid_lines = 0
    for line in lines:
        line = line.strip()
        if len(line) < 2:
            continue
        # Heuristic: A good line is likely to end with a period, or be a bullet point.
        if line.endswith(".") or line.startswith(("*", "-", "•", "●")):
            valid_lines += 1
        # Heuristic: A good line has a reasonable character-to-word ratio
        elif len(line) > 15 and " " in line:
            words = line.split()
            if len(words) > 2 and np.mean([len(w) for w in words]) < 15:
                valid_lines += 1

    return valid_lines / len(lines) if lines else 0.0


def get_best_extraction(pdf_path: Path) -> tuple[str, str, float, float]:
    """
    Extracts text using both libraries and returns the one with the higher quality score.
    Accepts PDF or image paths (png/jpg/jpeg/webp).
    """
    text_pymupdf = extract_text_pymupdf(pdf_path)
    score_pymupdf = score_extraction_quality(text_pymupdf)

    text_pdfplumber = extract_text_pdfplumber(pdf_path)
    score_pdfplumber = score_extraction_quality(text_pdfplumber)

    if score_pymupdf >= score_pdfplumber:
        return text_pymupdf, "PyMuPDF", score_pymupdf, score_pdfplumber
    return text_pdfplumber, "pdfplumber", score_pymupdf, score_pdfplumber


def classify_extraction_failure(path: Path) -> str:
    """
    Bucket a failed extraction for triage reporting.

    Returns one of: image_file | image_only_pdf | corrupted | empty_or_unreadable | unknown
    """
    suffix = path.suffix.lower()
    if not path.exists():
        return "missing_file"
    if suffix in IMAGE_EXTENSIONS:
        return "image_file"
    if suffix != ".pdf":
        return "unknown"
    try:
        with fitz.open(path) as doc:
            if doc.page_count == 0:
                return "corrupted"
            has_text = any((page.get_text() or "").strip() for page in doc)
            if has_text:
                return "empty_or_unreadable"
            # No text layer — scanned / image-only PDF
            return "image_only_pdf"
    except Exception:
        return "corrupted"


def triage_failures(
    assembled_path: Path = Path("data/structured/assembled.jsonl"),
    out_path: Path | None = Path("data/structured/extraction_triage.json"),
) -> dict:
    """
    Report why assembled threads fail text extraction.

    Buckets: image_file / image_only_pdf / corrupted / empty_or_unreadable / ok / no_pdf.
    """
    ensure_tessdata_prefix()
    rows = []
    with open(assembled_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))

    buckets: Counter[str] = Counter()
    details: list[dict] = []
    for row in rows:
        tid = row.get("thread_id")
        pdf = row.get("pdf_path")
        if not pdf:
            buckets["no_pdf"] += 1
            details.append({"thread_id": tid, "bucket": "no_pdf", "path": None})
            continue
        path = Path(pdf)
        text, lib, score_a, score_b = get_best_extraction(path)
        if text.strip():
            buckets["ok"] += 1
            details.append(
                {
                    "thread_id": tid,
                    "bucket": "ok",
                    "path": str(path),
                    "chars": len(text),
                    "lib": lib,
                    "score": max(score_a, score_b),
                }
            )
        else:
            bucket = classify_extraction_failure(path)
            buckets[bucket] += 1
            details.append({"thread_id": tid, "bucket": bucket, "path": str(path)})

    report = {
        "tesseract_available": tesseract_available(),
        "tessdata_prefix": os.environ.get("TESSDATA_PREFIX"),
        "n_threads": len(rows),
        "buckets": dict(buckets),
        "ok_rate": round(buckets["ok"] / len(rows), 4) if rows else 0.0,
        "details": details,
    }
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def compare_extractions(pdf_dir: Path, num_samples: int):
    """
    Prints a side-by-side comparison for N random sample resume files (PDF + images).
    """
    resume_files = [
        p for p in pdf_dir.rglob("*") if p.is_file() and p.suffix.lower() in RESUME_EXTENSIONS
    ]
    if not resume_files:
        print("No resume PDFs/images found to compare.")
        return

    sample_files = np.random.choice(
        resume_files, min(num_samples, len(resume_files)), replace=False
    )

    for i, pdf_path in enumerate(sample_files):
        print(f"--- Comparison Sample {i+1}: {pdf_path.name} ---")

        text_pymupdf = extract_text_pymupdf(pdf_path)
        score_pymupdf = score_extraction_quality(text_pymupdf)

        text_pdfplumber = extract_text_pdfplumber(pdf_path)
        score_pdfplumber = score_extraction_quality(text_pdfplumber)

        print(f"Score (PyMuPDF): {score_pymupdf:.2f}")
        print(f"Score (pdfplumber): {score_pdfplumber:.2f}")

        print("\n--- PyMuPDF ---")
        print((text_pymupdf[:500] + "...") if text_pymupdf else "(empty)")

        print("\n--- pdfplumber ---")
        print((text_pdfplumber[:500] + "...") if text_pdfplumber else "(empty)")
        print("-" * 20)


def main():
    parser = argparse.ArgumentParser(description="PDF/image text extraction for Roastproof.")
    parser.add_argument(
        "--input-dir",
        type=Path,
        default="data/raw",
        help="Directory containing raw resume files.",
    )
    parser.add_argument(
        "--compare",
        type=int,
        metavar="N",
        help="Print a side-by-side comparison for N random sample resumes.",
    )
    parser.add_argument(
        "--triage",
        action="store_true",
        help="Bucket extraction failures from assembled.jsonl (OCR readiness report).",
    )
    parser.add_argument(
        "--assembled",
        type=Path,
        default=Path("data/structured/assembled.jsonl"),
        help="Assembled JSONL used by --triage.",
    )
    args = parser.parse_args()

    if args.triage:
        report = triage_failures(args.assembled)
        print(f"tesseract_available={report['tesseract_available']}")
        print(f"TESSDATA_PREFIX={report['tessdata_prefix']}")
        print(f"n_threads={report['n_threads']} ok_rate={report['ok_rate']:.1%}")
        print("buckets:", report["buckets"])
        print("Wrote data/structured/extraction_triage.json")
        return

    if args.compare:
        compare_extractions(args.input_dir, args.compare)
    else:
        resume_files = [
            p
            for p in args.input_dir.rglob("*")
            if p.is_file() and p.suffix.lower() in RESUME_EXTENSIONS
        ]
        print(f"Found {len(resume_files)} resume files in {args.input_dir}.")
        for pdf_path in resume_files:
            text, lib, score_a, score_b = get_best_extraction(pdf_path)
            print(
                f"Processed {pdf_path.name}: Chose {lib} "
                f"(Score {max(score_a, score_b):.2f}, chars={len(text)})"
            )


if __name__ == "__main__":
    main()
