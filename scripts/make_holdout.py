import argparse
import json
import random
import shutil
from pathlib import Path

def create_holdout_set(raw_path: Path, holdout_path: Path, num_threads: int, seed: int):
    """
    Creates a holdout dataset by moving a random selection of threads.
    """
    print("--- Creating Holdout Set ---")

    # 1. Refuse to run if holdout path already exists and is not empty
    if holdout_path.exists() and any(holdout_path.iterdir()):
        print(f"Error: Holdout directory '{holdout_path}' already exists and is not empty.")
        print("Aborting to prevent overwriting the holdout set.")
        return

    # 2. Ensure raw data path exists
    if not raw_path.exists():
        print(f"Error: Raw data directory '{raw_path}' not found.")
        return

    # 3. Get all thread directories
    thread_dirs = [d for d in raw_path.iterdir() if d.is_dir()]
    if len(thread_dirs) < num_threads:
        print(f"Error: Not enough threads. Found {len(thread_dirs)}, but require {num_threads}.")
        return

    # 4. Seeded random selection
    random.seed(seed)
    holdout_threads = random.sample(thread_dirs, num_threads)
    holdout_thread_ids = [p.name for p in holdout_threads]

    # 5. Create holdout directory and move threads
    print(f"Selecting {num_threads} threads for the holdout set...")
    holdout_path.mkdir(exist_ok=True)

    for thread_path in holdout_threads:
        destination = holdout_path / thread_path.name
        print(f"  - Moving {thread_path.name} to {holdout_path}")
        shutil.move(str(thread_path), str(destination))

    # 6. Write manifest file
    manifest_path = holdout_path / "MANIFEST.json"
    manifest_content = {
        "description": "Holdout set for the Roastproof project.",
        "seed": seed,
        "num_threads": len(holdout_thread_ids),
        "thread_ids": sorted(holdout_thread_ids),
    }

    print(f"Writing manifest to {manifest_path}...")
    with open(manifest_path, "w") as f:
        json.dump(manifest_content, f, indent=2)

    print("\n--- Holdout Set Creation Complete ---")

def main():
    parser = argparse.ArgumentParser(description="Create a holdout dataset from raw data.")
    parser.add_argument(
        "--raw-path",
        type=str,
        default="data/raw",
        help="Path to the raw data directory.",
    )
    parser.add_argument(
        "--holdout-path",
        type=str,
        default="data/holdout",
        help="Path to the holdout data directory to be created.",
    )
    parser.add_argument(
        "--num-threads",
        type=int,
        default=100,
        help="Number of threads to include in the holdout set.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for reproducibility."
    )
    args = parser.parse_args()

    create_holdout_set(
        raw_path=Path(args.raw_path),
        holdout_path=Path(args.holdout_path),
        num_threads=args.num_threads,
        seed=args.seed,
    )

if __name__ == "__main__":
    main()
