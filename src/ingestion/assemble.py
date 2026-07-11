import argparse
import json
import re
from pathlib import Path

# Simple junk filtering rules
MIN_MESSAGE_LENGTH = 15
JUNK_PATTERNS = [
    re.compile(r"^\s*bump\s*$", re.IGNORECASE),
    re.compile(r"^[\s\W]*$"),  # Empty or punctuation-only
]
BOT_AUTHORS = ["some_bot_name"]  # Add known bot names here

# Scraper keeps Discord's original attachment filenames (SWE_Resume.pdf,
# resume-1.png, …) — it does NOT rename to a fixed resume.pdf.
RESUME_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}
SKIP_NAMES = {"post.txt", "critiques.txt", ".ds_store"}


def is_junk(message: str, author: str) -> bool:
    """Determines if a message is junk based on simple heuristics."""
    if author in BOT_AUTHORS:
        return True
    if len(message) < MIN_MESSAGE_LENGTH:
        return True
    for pattern in JUNK_PATTERNS:
        if pattern.match(message):
            return True
    # Basic emoji check (this is not comprehensive)
    if not re.search(r"[a-zA-Z]", message):
        return True
    return False


def _pick_resume_file(candidates: list[Path]) -> Path | None:
    """Prefer a PDF when multiple attachments exist; otherwise first match."""
    if not candidates:
        return None
    pdfs = [p for p in candidates if p.suffix.lower() == ".pdf"]
    return pdfs[0] if pdfs else candidates[0]


def resolve_resume_path(entry: dict, raw_dir: Path) -> Path | None:
    """
    Locate the resume attachment for a thread.

    Order of preference:
    1. Basenames from dataset.json's resume_files that exist under the thread folder
       (paths in dataset.json are scraper-relative like data/export/<id>/file.pdf —
       only the basename is meaningful after sync into data/raw/).
    2. Any resume-like file sitting in the thread folder (scan fallback).
    3. Legacy convention resume.pdf if present.
    """
    thread_id = str(entry["resume_message_id"])
    thread_dir = raw_dir / thread_id
    if not thread_dir.is_dir():
        return None

    from_manifest: list[Path] = []
    for rel in entry.get("resume_files") or []:
        name = Path(rel).name
        if not name or name.lower() in SKIP_NAMES:
            continue
        candidate = thread_dir / name
        if candidate.is_file() and candidate.suffix.lower() in RESUME_EXTENSIONS:
            from_manifest.append(candidate)

    picked = _pick_resume_file(from_manifest)
    if picked is not None:
        return picked

    scanned = sorted(
        p
        for p in thread_dir.iterdir()
        if p.is_file()
        and p.name.lower() not in SKIP_NAMES
        and p.suffix.lower() in RESUME_EXTENSIONS
    )
    picked = _pick_resume_file(scanned)
    if picked is not None:
        return picked

    legacy = thread_dir / "resume.pdf"
    return legacy if legacy.is_file() else None


def assemble_thread(entry: dict, raw_dir: Path) -> dict | None:
    """
    Assembles one dataset.json entry (the scraper's canonical structured
    export — see docs/phases/phase0-setup.md) into a raw JSON record.

    Resume file location comes from dataset.json resume_files (basename mapped
    into the synced thread folder) with a directory scan as fallback. The
    scraper preserves original Discord attachment names — there is no fixed
    resume.pdf convention on disk.

    Threads with no critiques are NOT dropped here — that decision (keep, but flag
    `no_critiques`) belongs to filter.py per the PRD. Threads with no resume file
    at all ARE dropped here, since there is nothing to structure.
    """
    thread_id = entry["resume_message_id"]

    resume_path = resolve_resume_path(entry, raw_dir)
    if resume_path is None:
        print(
            f"Skipping {thread_id}: no resume attachment found "
            "(text-only post or missing file)."
        )
        return None

    context_message = entry.get("post_message", "") or ""

    critiques = []
    for critique in entry.get("critiques", []):
        author = critique.get("author", "")
        content = critique.get("content", "")
        if not is_junk(content, author):
            critiques.append({
                "author": author,
                "content": content,
                "timestamp": critique.get("timestamp"),
            })

    return {
        "thread_id": thread_id,
        # Kept as pdf_path for downstream compatibility; may be png/jpg/webp.
        "pdf_path": str(resume_path),
        "context_message": context_message,
        "critiques": critiques,
    }


def assemble_from_dataset(dataset_path: Path, raw_dir: Path) -> list[dict]:
    entries = json.loads(dataset_path.read_text(encoding="utf-8"))
    records = []
    for entry in entries:
        record = assemble_thread(entry, raw_dir)
        if record:
            records.append(record)
    return records


def main():
    parser = argparse.ArgumentParser(description="Assemble raw data into structured JSON records.")
    parser.add_argument(
        "--input-dir",
        type=Path,
        default="data/raw",
        help="Directory containing raw thread data (dataset.json + per-thread folders).",
    )
    parser.add_argument(
        "--output-file",
        type=Path,
        default="data/structured/assembled.jsonl",
        help="File to write the assembled JSONL records to.",
    )
    args = parser.parse_args()

    args.output_file.parent.mkdir(parents=True, exist_ok=True)

    dataset_path = args.input_dir / "dataset.json"
    if not dataset_path.exists():
        raise FileNotFoundError(
            f"{dataset_path} not found. Run scripts/sync_scraper_data.py first to populate {args.input_dir}."
        )

    all_entries = json.loads(dataset_path.read_text(encoding="utf-8"))
    assembled_count = 0
    with open(args.output_file, "w", encoding="utf-8") as f:
        for entry in all_entries:
            record = assemble_thread(entry, args.input_dir)
            if record:
                f.write(json.dumps(record) + "\n")
                assembled_count += 1

    print("--- Assembly Complete ---")
    print(f"Processed {len(all_entries)} threads.")
    print(f"Assembled {assembled_count} valid records into {args.output_file}")


if __name__ == "__main__":
    main()
