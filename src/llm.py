"""
Shared LLM client wrapper (G5).

All LLM calls in the project go through this module — no raw OpenAI client
usage elsewhere. Every call is appended to logs/llm_calls.jsonl.

Provider: OpenAI (OPENAI_API_KEY).
Model roles (G3 cost control):
  - Bulk / structuring: gpt-4o-mini  (Haiku-class: cheap + fast)
  - Synthesis / generation (later phases): gpt-4o  (Sonnet-class)
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Type, TypeVar

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

# Load repo-root .env so OPENAI_API_KEY is available without exporting it
# in the shell. Does not override a key already set in the environment.
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

LOG_FILE = "logs/llm_calls.jsonl"
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

# Default model aliases used across the pipeline.
MODEL_BULK = "gpt-4o-mini"
MODEL_SYNTHESIS = "gpt-4o"


def _client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Add it to the repo-root .env file."
        )
    return OpenAI(api_key=api_key)


def _log_llm_call(phase: str, model: str, prompt: str, response: str, usage: dict) -> None:
    """Appends a log entry to the LLM calls log file (G5)."""
    with open(LOG_FILE, "a") as f:
        log_entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "phase": phase,
            "model": model,
            "prompt": prompt,
            "response": response,
            "usage": usage,
        }
        f.write(json.dumps(log_entry) + "\n")


def _usage_dict(usage) -> dict:
    if usage is None:
        return {}
    return {
        "input_tokens": getattr(usage, "prompt_tokens", None),
        "output_tokens": getattr(usage, "completion_tokens", None),
    }


def _extract_json(text: str) -> str:
    """Strip optional markdown fences so Pydantic can parse the payload."""
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```$", text)
    if fence:
        return fence.group(1).strip()
    # Prose wrappers: take the outermost JSON object.
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]
    return text


def complete(
    prompt: str,
    model: str,
    phase: str,
    max_tokens: int,
    system: str | None = None,
) -> str:
    """
    Calls the OpenAI Chat Completions API.

    Args:
        prompt: The user prompt to complete.
        model: The model to use (e.g. gpt-4o-mini).
        phase: The current development phase (for logging, G5).
        max_tokens: The maximum number of tokens to generate.
        system: An optional system prompt.

    Returns:
        The completed text.
    """
    client = _client()
    messages: list[dict] = []
    if system is not None:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    response = client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
    )
    content = response.choices[0].message.content or ""
    usage = _usage_dict(response.usage)
    _log_llm_call(phase, model, prompt, content, usage)
    return content


def complete_json(
    prompt: str,
    model: str,
    phase: str,
    schema: Type[T],
) -> T:
    """
    Calls OpenAI and parses the JSON response into a Pydantic schema.

    Uses response_format=json_object. Retries once on validation failure with
    the error appended to the prompt.
    """
    client = _client()
    messages: list[dict] = [
        {
            "role": "system",
            "content": (
                "You are a careful JSON generator. Respond with a single valid "
                "JSON object and nothing else — no markdown fences, no commentary."
            ),
        },
        {"role": "user", "content": prompt},
    ]

    for _ in range(2):  # initial attempt + one retry
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=4096,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content or ""
        usage = _usage_dict(response.usage)
        _log_llm_call(phase, model, prompt, content, usage)

        try:
            return schema.model_validate_json(_extract_json(content))
        except ValidationError as e:
            error_message = (
                f"JSON validation failed with the following errors:\n{e}\n\n"
                "Please correct the JSON and try again. Return only valid JSON."
            )
            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content": error_message})

    raise ValueError("Failed to complete JSON after one retry.")


def batch_complete(
    requests: list[dict],
    model: str,
    phase: str,
    poll_interval: float = 5.0,
    poll_timeout: float = 3600.0,
) -> list[str | None]:
    """
    Runs a batch of prompts through the OpenAI Batch API (G3 cost control).

    Args:
        requests: List of dicts, each with at least {"custom_id": str, "prompt": str,
            "max_tokens": int} and optionally {"system": str}. `custom_id` must be
            unique within the batch; if omitted, the request's index is used.
        model: The model to use for every request in the batch.
        phase: The current development phase (for logging, G5).
        poll_interval: Seconds to sleep between batch status checks.
        poll_timeout: Give up (raise) if the batch hasn't ended within this many seconds.

    Returns:
        List of response text strings in the same order as `requests`. An entry is
        None if that particular request errored, expired, or was cancelled.
    """
    client = _client()

    # Small batches: run synchronously — OpenAI Batch has a 24h completion window
    # and is overkill for pilot-sized runs. Keeps the same return shape.
    if len(requests) <= 20:
        results: list[str | None] = []
        for req in requests:
            try:
                text = complete(
                    prompt=req["prompt"],
                    model=model,
                    phase=phase,
                    max_tokens=req["max_tokens"],
                    system=req.get("system"),
                )
                results.append(text)
            except Exception as e:
                _log_llm_call(phase, model, req.get("prompt", "[batch]"), f"<error: {e}>", {})
                results.append(None)
        return results

    lines: list[str] = []
    for i, req in enumerate(requests):
        custom_id = str(req.get("custom_id", i))
        messages: list[dict] = []
        if req.get("system") is not None:
            messages.append({"role": "system", "content": req["system"]})
        messages.append({"role": "user", "content": req["prompt"]})
        body = {
            "model": model,
            "messages": messages,
            "max_tokens": req["max_tokens"],
        }
        lines.append(
            json.dumps(
                {
                    "custom_id": custom_id,
                    "method": "POST",
                    "url": "/v1/chat/completions",
                    "body": body,
                }
            )
        )

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write("\n".join(lines))
        tmp_path = tmp.name

    try:
        with open(tmp_path, "rb") as f:
            input_file = client.files.create(file=f, purpose="batch")

        batch = client.batches.create(
            input_file_id=input_file.id,
            endpoint="/v1/chat/completions",
            completion_window="24h",
        )
        print(f"OpenAI Batch submitted: {batch.id} (status={batch.status})")

        elapsed = 0.0
        while batch.status not in {"completed", "failed", "expired", "cancelled"}:
            if elapsed >= poll_timeout:
                raise TimeoutError(
                    f"Batch {batch.id} did not end within {poll_timeout}s "
                    f"(status: {batch.status})"
                )
            time.sleep(poll_interval)
            elapsed += poll_interval
            batch = client.batches.retrieve(batch.id)
            if int(elapsed) % 30 < poll_interval:
                print(
                    f"  batch {batch.id}: status={batch.status} "
                    f"elapsed={int(elapsed)}s"
                )

        results_by_id: dict[str, str | None] = {}
        if batch.status != "completed" or not batch.output_file_id:
            _log_llm_call(
                phase, model, "[batch]", f"<batch {batch.status}>", {}
            )
            return [None] * len(requests)

        output_text = client.files.content(batch.output_file_id).text
        for line in output_text.splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            custom_id = row["custom_id"]
            resp = row.get("response") or {}
            body = resp.get("body") or {}
            choices = body.get("choices") or []
            if choices:
                content = choices[0].get("message", {}).get("content") or ""
                usage_raw = body.get("usage") or {}
                usage = {
                    "input_tokens": usage_raw.get("prompt_tokens"),
                    "output_tokens": usage_raw.get("completion_tokens"),
                }
                _log_llm_call(phase, model, "[batch]", content, usage)
                results_by_id[custom_id] = content
            else:
                _log_llm_call(phase, model, "[batch]", "<no choices>", {})
                results_by_id[custom_id] = None

        return [
            results_by_id.get(str(req.get("custom_id", i)))
            for i, req in enumerate(requests)
        ]
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
