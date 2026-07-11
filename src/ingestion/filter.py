import argparse
from pathlib import Path
from ..schemas import ThreadRecord, TargetRole
from .pdf_extract import score_extraction_quality

def get_funnel_counts(raw_path, assembled_path, structured_path):
    """Gathers counts for the funnel report."""
    raw_count = len([d for d in raw_path.iterdir() if d.is_dir()])

    with open(assembled_path, 'r') as f:
        assembled_count = sum(1 for _ in f)

    with open(structured_path, 'r') as f:
        structured_count = sum(1 for _ in f)

    return raw_count, assembled_count, structured_count

def filter_structured_data(
    structured_records: list[ThreadRecord],
) -> list[ThreadRecord]:
    """
    Filters and applies quality flags to structured records.
    """
    final_records = []
    for record in structured_records:
        # Set quality flags
        if not record.critiques:
            record.quality_flags.no_critiques = True

        if record.target_role in [TargetRole.PRODUCT_MANAGER]: # Add other non-CS roles if needed
            record.quality_flags.non_cs_role = True

        # Re-calculate extraction quality score for simplicity
        extraction_score = score_extraction_quality(record.resume_text)
        if extraction_score < 0.5: # Example threshold
            record.quality_flags.low_quality_extraction = True

        # Exclude records that failed parsing (should already be handled by structure.py)
        if record.quality_flags.parse_failed:
            continue

        final_records.append(record)

    return final_records

def main():
    parser = argparse.ArgumentParser(description="Filter and finalize structured data.")
    parser.add_argument(
        "--input-file",
        type=Path,
        default="data/structured/structured.jsonl",
        help="Input JSONL file with structured data.",
    )
    parser.add_argument(
        "--output-file",
        type=Path,
        default="data/structured/threads.jsonl",
        help="Final output JSONL file.",
    )
    parser.add_argument(
        "--raw-path",
        type=Path,
        default="data/raw",
    )
    parser.add_argument(
        "--assembled-path",
        type=Path,
        default="data/structured/assembled.jsonl",
    )
    args = parser.parse_args()

    # Load structured data
    with open(args.input_file, "r") as f:
        structured_records = [ThreadRecord.model_validate_json(line) for line in f]

    # Filter and apply quality flags
    final_records = filter_structured_data(structured_records)

    # Write final output
    with open(args.output_file, "w") as f:
        for record in final_records:
            f.write(record.model_dump_json() + "\n")

    # Print funnel report
    raw_count, assembled_count, structured_count = get_funnel_counts(
        args.raw_path, args.assembled_path, args.input_file
    )
    clean_count = len(final_records)

    print("--- Filtering Complete ---")
    print("\n--- Ingestion Funnel Report ---")
    print(f"  Raw Threads:      {raw_count}")
    print(f"  Assembled Records:{assembled_count}")
    print(f"  Structured (LLM): {structured_count}")
    print(f"  Final Clean:      {clean_count}")

    # Acceptance Criteria Check
    if raw_count > 0:
        survival_rate = clean_count / raw_count
        print(f"\nSurvival Rate: {survival_rate:.2%}")
        if survival_rate < 0.85:
            print("Warning: Survival rate is below the 85% target.")
        else:
            print("Survival rate is above the 85% target.\n")

if __name__ == "__main__":
    main()
