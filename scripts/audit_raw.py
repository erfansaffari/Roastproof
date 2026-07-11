import argparse
import random
import re
from pathlib import Path

# G2 PII Regexes (add more as needed)
PII_REGEXES = {
    "EMAIL": re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+"),
    "PHONE": re.compile(r"\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}"),
    # A simple check for URLs that might contain usernames, not exhaustive.
    "USER_URL": re.compile(r"https?://(www\.)?(linkedin\.com/in/|github\.com/)[a-zA-Z0-9_-]+/?"),
}

def audit_pii(text: str) -> dict[str, int]:
    """Scans text for PII matches and returns hit counts."""
    hits = {key: 0 for key in PII_REGEXES}
    for key, regex in PII_REGEXES.items():
        hits[key] = len(regex.findall(text))
    return hits

def audit_raw_data(raw_data_path: Path):
    """
    Audits the raw data directory.

    - Counts threads, PDFs, and messages.
    - Prints 5 random thread samples.
    - Runs a PII regex sweep in report-only mode and prints hit counts.
    """
    print(f"--- Auditing Raw Data at: {raw_data_path} ---")

    if not raw_data_path.exists():
        print(f"Error: Raw data directory not found at '{raw_data_path}'")
        print("Please ensure data is populated (see scripts/sync_scraper_data.py).")
        return

    # Each subdirectory in raw_data_path is a thread (dataset.json sits alongside as a file).
    thread_dirs = [d for d in raw_data_path.iterdir() if d.is_dir()]
    thread_count = len(thread_dirs)
    print(f"\nFound {thread_count} threads.")

    if thread_count == 0:
        print("No threads to audit.")
        return

    # --- File Counts ---
    total_files = 0
    pdf_files = 0
    for thread_dir in thread_dirs:
        files = list(thread_dir.iterdir())
        total_files += len(files)
        pdf_files += len([f for f in files if f.suffix.lower() == '.pdf'])

    print(f"Found {total_files} total files (messages, attachments, etc.).")
    print(f"Found {pdf_files} PDF files.")
    if pdf_files < thread_count:
        print(f"Note: {thread_count - pdf_files} threads have no resume PDF (text-only post, or missing file).")

    # --- Random Samples ---
    print("\n--- 5 Random Thread Samples ---")
    num_samples = min(thread_count, 5)
    sample_dirs = random.sample(thread_dirs, num_samples)

    for i, thread_dir in enumerate(sample_dirs):
        print(f"\nSample {i+1}: {thread_dir.name}")
        files_in_thread = [f.name for f in thread_dir.iterdir()]
        print(f"  Files: {files_in_thread}")
        first_text_file = next((f for f in thread_dir.iterdir() if f.suffix in ['.txt', '.md']), None)
        if first_text_file:
            content = first_text_file.read_text(encoding='utf-8', errors='ignore')
            preview = content[:150].replace('\n', ' ')
            print(f"  Content of {first_text_file.name} (first 150 chars): '{preview}...'")

    # --- PII Sweep ---
    print("\n--- PII Sweep (Report-Only) ---")
    total_pii_hits = {key: 0 for key in PII_REGEXES}

    for thread_dir in thread_dirs:
        for file_path in thread_dir.glob("*"):
            if file_path.is_file() and file_path.suffix not in ['.pdf']:  # Simple text file check
                try:
                    content = file_path.read_text(encoding='utf-8', errors='ignore')
                    pii_hits = audit_pii(content)
                    for key, count in pii_hits.items():
                        if count > 0:
                            total_pii_hits[key] += count
                except Exception as e:
                    print(f"Could not read {file_path}: {e}")

    print("Total PII hits found across all non-PDF files:")
    for key, count in total_pii_hits.items():
        print(f"  - {key}: {count}")

    print("\n--- Audit Complete ---")


def main():
    parser = argparse.ArgumentParser(description="Audit the raw data directory.")
    parser.add_argument(
        "--path",
        type=str,
        default="data/raw",
        help="Path to the raw data directory.",
    )
    args = parser.parse_args()
    audit_raw_data(Path(args.path))

if __name__ == "__main__":
    main()
