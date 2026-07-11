"""
Copies the scraper's export (src/scraper/data/export/) into data/raw/, the
location the ingestion pipeline (Phase 1) and audit/holdout scripts (Phase 0)
expect.

Deliberately a separate, explicit step rather than pointing ingestion directly
at src/scraper/data/export/:
- data/raw/ is the pipeline's frozen input contract; the scraper's export
  directory is a live working area that gets rewritten every time someone runs
  `python -m bot.main export`. Ingestion should read a deliberate snapshot, not
  a directory that can change underneath it mid-run.
- Keeps ingestion code decoupled from the scraper's internal layout — if the
  scraper is rewritten or replaced, only this sync step needs to change.
- Matches data/raw/'s existing role as the source make_holdout.py freezes
  100 threads out of; you want to control exactly when new data enters that pool.

Copies dataset.json plus each per-thread folder (resume attachment under its
original Discord filename if present, post.txt, critiques.txt). Existing files
in data/raw/ are overwritten by newer copies from the export; nothing in
data/raw/ is deleted first, so threads already frozen into data/holdout/
(moved out of data/raw/ by make_holdout.py) are left alone.
"""

import argparse
import shutil
from pathlib import Path

SKIP_NAMES = {".DS_Store"}


def sync(export_dir: Path, raw_dir: Path) -> int:
    if not export_dir.exists():
        raise FileNotFoundError(f"Scraper export not found at {export_dir}. Run `python -m bot.main export` first.")

    raw_dir.mkdir(parents=True, exist_ok=True)

    dataset_json = export_dir / "dataset.json"
    if dataset_json.exists():
        shutil.copy2(dataset_json, raw_dir / "dataset.json")

    synced = 0
    for thread_dir in export_dir.iterdir():
        if not thread_dir.is_dir() or thread_dir.name in SKIP_NAMES:
            continue

        dest_dir = raw_dir / thread_dir.name
        dest_dir.mkdir(parents=True, exist_ok=True)
        for item in thread_dir.iterdir():
            if item.name in SKIP_NAMES:
                continue
            shutil.copy2(item, dest_dir / item.name)
        synced += 1

    return synced


def main():
    parser = argparse.ArgumentParser(description="Sync scraper export into data/raw/.")
    parser.add_argument(
        "--export-dir",
        type=Path,
        default=Path("src/scraper/data/export"),
        help="Scraper export directory.",
    )
    parser.add_argument(
        "--raw-dir",
        type=Path,
        default=Path("data/raw"),
        help="Pipeline raw-data directory to sync into.",
    )
    args = parser.parse_args()

    synced = sync(args.export_dir, args.raw_dir)
    print(f"Synced {synced} thread folders (+ dataset.json) into {args.raw_dir}")


if __name__ == "__main__":
    main()
