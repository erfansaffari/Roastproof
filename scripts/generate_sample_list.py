import argparse
import random
from pathlib import Path

def generate_sample_list(raw_path: Path, num_samples: int, seed: int):
    """
    Generates a list of random thread samples for manual review.
    """
    if not raw_path.exists():
        print(f"Error: Raw data directory not found at '{raw_path}'")
        return

    thread_dirs = [d for d in raw_path.iterdir() if d.is_dir()]
    if len(thread_dirs) < num_samples:
        print(f"Warning: Not enough threads to generate the desired number of samples. Found {len(thread_dirs)}, want {num_samples}.")
        num_samples = len(thread_dirs)

    random.seed(seed)
    sample_threads = random.sample(thread_dirs, num_samples)
    
    print(f"--- {num_samples} Random Threads for Manual Review (seed={seed}) ---")
    print("Please review the contents of these thread directories and record your observations in NOTES.md.")
    
    for thread_path in sorted(sample_threads):
        print(thread_path.name)

def main():
    parser = argparse.ArgumentParser(description="Generate a list of random thread samples for manual review.")
    parser.add_argument(
        "--raw-path",
        type=str,
        default="data/raw",
        help="Path to the raw data directory.",
    )
    parser.add_argument(
        "--num-samples",
        type=int,
        default=20,
        help="Number of random thread samples to generate.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=123, # Different seed from holdout script
        help="Random seed for reproducibility."
    )
    args = parser.parse_args()

    generate_sample_list(
        raw_path=Path(args.raw_path),
        num_samples=args.num_samples,
        seed=args.seed,
    )

if __name__ == "__main__":
    main()
