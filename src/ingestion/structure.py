import argparse
import json
import re
from pathlib import Path
from typing import List, Dict

from tqdm import tqdm
from .. import llm
from ..schemas import ThreadRecord, TargetRole, Critique
from .pdf_extract import get_best_extraction

# --- PII Redaction ---
PII_REGEXES = {
    "EMAIL": re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+"),
    "PHONE": re.compile(r"\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}"),
    "USER_URL": re.compile(r"https?://(www\.)?(linkedin\.com/in/|github\.com/)[a-zA-Z0-9_-]+/?"),
}

def redact_pii(text: str) -> str:
    """Redacts PII from a string."""
    for key, regex in PII_REGEXES.items():
        text = regex.sub(f"[{key}_REDACTED]", text)
    return text

# --- LLM Structuring ---

# NOTE: The few-shot examples should be populated based on NOTES.md observations.
FEW_SHOT_EXAMPLES = """
[FEW_SHOT_EXAMPLE_1]
[FEW_SHOT_EXAMPLE_2]
"""

PROMPT_TEMPLATE = """
You are an expert resume analyst. Your task is to structure raw data from a resume critique thread into a strict JSON object.

The user will provide:
- The full text of a resume.
- The original poster's context message.
- A list of critiques from the community.

Return ONLY a JSON object (no markdown fences, no commentary) with this shape:
{{
  "thread_id": "<string, copy from input>",
  "target_role": "<ONE of: __TARGET_ROLES__>",
  "applicant_profile": "<short background summary>",
  "resume_text": "<copy the full resume text>",
  "resume_sections": {{}},
  "context_message": "<copy the context message>",
  "critiques": [
    {{
      "author": "<name>",
      "content": "<full critique text>",
      "agreement_signal": 0,
      "original_text": null
    }}
  ],
  "quality_flags": {{
    "parse_failed": false,
    "no_critiques": false,
    "non_cs_role": false,
    "low_quality_extraction": false
  }}
}}

Rules:
1. `target_role` MUST be exactly one of the enum values listed above.
2. `agreement_signal` = count of other users agreeing (+1, "this", "^", etc.).
3. `original_text` = quoted resume snippet if the critique clearly quotes one; else null.
4. Do NOT echo JSON Schema / $defs. Return the data object only.

Here are some examples of how to structure the data:
__FEW_SHOT__

Now, please structure the following data:

**Thread ID**: __THREAD_ID__
**Resume Text**:
---
__RESUME_TEXT__
---
**Context Message**:
---
__CONTEXT_MESSAGE__
---
**Raw Critiques**:
---
__CRITIQUES__
---
"""


# Per G3: bulk per-thread work uses the cheap/fast model. OpenAI stand-in for
# Claude Haiku: gpt-4o-mini.
STRUCTURE_MODEL = "gpt-4o-mini"


def _apply_pii_redaction(record: ThreadRecord) -> ThreadRecord:
    """PII sweep (G2) over all free-text fields, in place."""
    record.resume_text = redact_pii(record.resume_text)
    record.context_message = redact_pii(record.context_message or "")
    for critique in record.critiques:
        critique.content = redact_pii(critique.content)
        if critique.original_text:
            critique.original_text = redact_pii(critique.original_text)
    return record


def build_structure_prompt(thread_data: Dict, resume_text: str) -> str:
    critique_texts = [f"{c['author']}:: {c['content']}" for c in thread_data["critiques"]]
    return (
        PROMPT_TEMPLATE
        .replace("__TARGET_ROLES__", ", ".join(e.value for e in TargetRole))
        .replace("__FEW_SHOT__", FEW_SHOT_EXAMPLES)
        .replace("__THREAD_ID__", str(thread_data["thread_id"]))
        .replace("__RESUME_TEXT__", resume_text)
        .replace("__CONTEXT_MESSAGE__", thread_data.get("context_message") or "")
        .replace("__CRITIQUES__", "\n".join(critique_texts))
    )


def structure_thread_with_llm(
    thread_data: Dict,
    resume_text: str,
) -> ThreadRecord | None:
    """
    Uses the LLM to structure a single thread synchronously (pilot mode only —
    full runs go through `structure_threads_batch` per G3).
    """
    prompt = build_structure_prompt(thread_data, resume_text)

    try:
        structured_data = llm.complete_json(
            prompt=prompt,
            model=STRUCTURE_MODEL,
            phase="phase1-structure",
            schema=ThreadRecord,
        )
        return _apply_pii_redaction(structured_data)

    except Exception as e:
        print(f"LLM structuring failed for thread {thread_data['thread_id']}: {e}")
        return None


def structure_threads_batch(
    thread_data_list: List[Dict],
    resume_texts: List[str],
) -> List[ThreadRecord | None]:
    """
    Structures many threads.

    For corpora up to SYNC_BATCH_LIMIT, runs complete_json per thread with a
    progress bar (visible, reliable JSON mode). Larger corpora use
    llm.batch_complete (OpenAI Batch API — cheaper, but can sit for a long
    time with little feedback).
    """
    # Sync path: OpenAI Batch has a 24h window and no response_format=json_object,
    # which previously produced markdown-wrapped / unusable payloads. Prefer sync
    # until the corpus is large enough that Batch cost savings matter.
    SYNC_BATCH_LIMIT = 250

    if len(thread_data_list) <= SYNC_BATCH_LIMIT:
        results: List[ThreadRecord | None] = []
        for thread_data, resume_text in tqdm(
            list(zip(thread_data_list, resume_texts)),
            desc="Structuring threads",
        ):
            results.append(structure_thread_with_llm(thread_data, resume_text))
        return results

    print(
        f"Corpus size {len(thread_data_list)} > {SYNC_BATCH_LIMIT}; "
        "using OpenAI Batch API (may take a while — status polls every 15s)."
    )
    requests = [
        {
            "custom_id": thread_data["thread_id"],
            "prompt": build_structure_prompt(thread_data, resume_text),
            "max_tokens": 4096,
            "system": (
                "You are a careful JSON generator. Respond with a single valid "
                "JSON object and nothing else — no markdown fences, no commentary."
            ),
        }
        for thread_data, resume_text in zip(thread_data_list, resume_texts)
    ]

    responses = llm.batch_complete(requests, model=STRUCTURE_MODEL, phase="phase1-structure")

    results = []
    for thread_data, content in zip(thread_data_list, responses):
        if content is None:
            print(f"Batch structuring failed for thread {thread_data['thread_id']}: no result")
            results.append(None)
            continue
        try:
            record = ThreadRecord.model_validate_json(llm._extract_json(content))
            results.append(_apply_pii_redaction(record))
        except Exception as e:
            print(f"Batch structuring returned invalid JSON for thread {thread_data['thread_id']}: {e}")
            results.append(None)

    return results

def main():
    parser = argparse.ArgumentParser(description="Structure assembled data using an LLM.")
    parser.add_argument(
        "--input-file",
        type=Path,
        default="data/structured/assembled.jsonl",
        help="Input JSONL file with assembled raw data.",
    )
    parser.add_argument(
        "--output-file",
        type=Path,
        default="data/structured/structured.jsonl",
        help="Output JSONL file for structured ThreadRecords.",
    )
    parser.add_argument(
        "--pilot",
        type=int,
        metavar="N",
        help="Run in pilot mode on N threads and write to a separate pilot file.",
    )
    parser.add_argument(
        "--only-missing",
        action="store_true",
        help=(
            "Incremental mode: load existing output-file thread_ids and only "
            "structure assembled threads not already present; append results."
        ),
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the cost-estimate confirmation gate for full (>100 call) Batch API runs (G3).",
    )
    args = parser.parse_args()

    # Load assembled data
    with open(args.input_file, "r") as f:
        thread_data_list = [json.loads(line) for line in f]

    existing_ids: set[str] = set()
    if args.only_missing and args.output_file.exists() and not args.pilot:
        with open(args.output_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    existing_ids.add(json.loads(line)["thread_id"])
                except (json.JSONDecodeError, KeyError):
                    continue
        before = len(thread_data_list)
        thread_data_list = [t for t in thread_data_list if t.get("thread_id") not in existing_ids]
        print(
            f"--- --only-missing: {len(existing_ids)} already structured; "
            f"considering {len(thread_data_list)}/{before} remaining ---"
        )

    if args.pilot:
        thread_data_list = thread_data_list[:args.pilot]
        args.output_file = args.output_file.with_name("pilot.jsonl")
        print(f"--- Running in PILOT mode on {args.pilot} threads (synchronous, not Batch API) ---")

        with open(args.output_file, "w") as f:
            for thread_data in tqdm(thread_data_list, desc="Structuring threads"):
                pdf_path = Path(thread_data["pdf_path"]) if thread_data.get("pdf_path") else None
                if pdf_path is None:
                    print(f"Skipping {thread_data['thread_id']}: no resume PDF for this thread.")
                    continue
                resume_text, _, _, _ = get_best_extraction(pdf_path)
                if not resume_text:
                    print(f"Skipping {thread_data['thread_id']}: Could not extract text from PDF.")
                    continue

                structured_record = structure_thread_with_llm(thread_data, resume_text)
                if structured_record:
                    f.write(structured_record.model_dump_json() + "\n")
    else:
        # Full run: extract text first; for small corpora use sync (see llm.batch_complete).
        usable, resume_texts = [], []
        for thread_data in thread_data_list:
            pdf_path = Path(thread_data["pdf_path"]) if thread_data.get("pdf_path") else None
            if pdf_path is None:
                print(f"Skipping {thread_data['thread_id']}: no resume PDF for this thread.")
                continue
            resume_text, _, _, _ = get_best_extraction(pdf_path)
            if not resume_text:
                print(f"Skipping {thread_data['thread_id']}: Could not extract text from PDF.")
                continue
            usable.append(thread_data)
            resume_texts.append(resume_text)

        est_calls = len(usable)
        if est_calls == 0:
            print("Nothing to structure (no extractable resumes in remaining set).")
            return
        if est_calls > 100 and not args.yes:
            print(f"About to submit {est_calls} threads to the Batch API (~{est_calls * 4}K input tokens, rough estimate).")
            print("Re-run with --yes to proceed.")
            return

        print(f"--- Structuring {est_calls} threads ---")
        structured_records = structure_threads_batch(usable, resume_texts)

        mode = "a" if args.only_missing and existing_ids else "w"
        written = 0
        with open(args.output_file, mode) as f:
            for record in structured_records:
                if record:
                    f.write(record.model_dump_json() + "\n")
                    written += 1

        print(f"Appended {written} records" if mode == "a" else f"Wrote {written} records")

    print(f"--- Structuring Complete ---")
    print(f"Structured records written to {args.output_file}")


if __name__ == "__main__":
    main()
