import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from bot import db
from bot.attachments import download_attachments, filter_supported_attachments
from bot.client import DiscordClient
from bot.config import RESUMES_DIR

logger = logging.getLogger(__name__)


def author_display_name(author: dict[str, Any]) -> str:
    return author.get("global_name") or author.get("username") or "unknown"


def build_critique_content(message: dict[str, Any]) -> str:
    content = message.get("content") or ""
    supported = filter_supported_attachments(message.get("attachments", []))

    if supported:
        attachment_note = ", ".join(attachment["url"] for attachment in supported)
        if content:
            content = f"{content}\n[attachments: {attachment_note}]"
        else:
            content = f"[attachments: {attachment_note}]"

    return content


def normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def reply_parent_message_id(message: dict[str, Any]) -> str | None:
    message_reference = message.get("message_reference") or {}
    parent_id = message_reference.get("message_id")
    if parent_id:
        return str(parent_id)
    return None


def resume_attachment_names(resume: dict[str, Any]) -> list[str]:
    paths = json.loads(resume.get("attachment_paths") or "[]")
    return [Path(path).name for path in paths]


async def try_insert_critique(
    message: dict[str, Any],
    resume_message_id: str,
) -> bool:
    if message["author"].get("bot"):
        return False

    if message["id"] == resume_message_id:
        return False

    if db.critique_exists(message["id"]):
        return False

    content = build_critique_content(message)
    if not content.strip():
        return False

    inserted = db.insert_critique(
        resume_message_id=resume_message_id,
        message_id=message["id"],
        author_id=message["author"]["id"],
        author_name=author_display_name(message["author"]),
        content=content,
        posted_at=message["timestamp"],
    )

    if inserted:
        logger.info(
            "Critique logged: %s <- %s",
            resume_message_id,
            author_display_name(message["author"]),
        )

    return inserted


async def backfill_resume_messages(
    client: DiscordClient,
    channel_id: str,
) -> int:
    updated = 0

    async for message in client.iter_channel_messages(channel_id):
        if message["author"].get("bot"):
            continue

        if not filter_supported_attachments(message.get("attachments", [])):
            continue

        message_id = message["id"]
        if not db.resume_exists(message_id):
            continue

        content = message.get("content") or ""
        if content and db.update_resume_message_content(message_id, content):
            updated += 1

    return updated


async def download_new_resumes(
    client: DiscordClient,
    channel_id: str,
    *,
    limit: int | None = None,
) -> int:
    resumes_added = 0
    archived_thread_ids = {
        thread["id"]
        for thread in await client.get_channel_archived_threads(channel_id)
    }

    async for message in client.iter_channel_messages(channel_id):
        if message["author"].get("bot"):
            continue

        supported = filter_supported_attachments(message.get("attachments", []))
        if not supported:
            continue

        message_id = message["id"]
        message_content = message.get("content") or ""

        if db.resume_exists(message_id):
            db.update_resume_message_content(message_id, message_content)
            continue

        if limit is not None and resumes_added >= limit:
            logger.info("Reached resume download limit (%s).", limit)
            break

        dest_dir = RESUMES_DIR / message_id
        attachment_paths = await download_attachments(client, supported, dest_dir)

        thread_id = message_id if message_id in archived_thread_ids else ""

        inserted = db.insert_resume(
            message_id=message_id,
            author_id=message["author"]["id"],
            author_name=author_display_name(message["author"]),
            thread_id=thread_id,
            posted_at=message["timestamp"],
            attachment_paths=attachment_paths,
            message_content=message_content,
        )

        if inserted:
            resumes_added += 1
            logger.info(
                "Resume captured: %s by %s (thread %s)",
                message_id,
                author_display_name(message["author"]),
                thread_id or "pending",
            )

    return resumes_added


async def scan_channel_replies(
    client: DiscordClient,
    channel_id: str,
) -> int:
    critiques_added = 0
    resume_message_ids = db.get_resume_message_ids()

    async for message in client.iter_channel_messages(channel_id):
        parent_message_id = reply_parent_message_id(message)
        if not parent_message_id or parent_message_id not in resume_message_ids:
            continue

        if await try_insert_critique(message, parent_message_id):
            critiques_added += 1

    return critiques_added


async def map_threads_to_resumes(
    client: DiscordClient,
    channel_id: str,
) -> dict[str, str]:
    archived_threads = await client.get_channel_archived_threads(channel_id)
    archived_by_id = {thread["id"]: thread for thread in archived_threads}
    thread_by_message: dict[str, str] = {}
    resumes = db.get_all_resumes()

    for resume in resumes:
        message_id = resume["message_id"]
        if resume.get("thread_id"):
            thread_by_message[message_id] = resume["thread_id"]
            continue

        if message_id in archived_by_id:
            thread_by_message[message_id] = message_id
            db.update_resume_thread_id(message_id, message_id)
            continue

        posted_at = parse_timestamp(resume["posted_at"])
        normalized_names = {
            normalize_name(name) for name in resume_attachment_names(resume)
        }
        best_thread_id = ""
        best_score = float("inf")

        for thread in archived_threads:
            thread_name = normalize_name(thread.get("name", ""))
            if not thread_name:
                continue

            name_matches = any(
                thread_name == normalize_name(name)
                or thread_name in normalize_name(name)
                or normalize_name(name) in thread_name
                for name in resume_attachment_names(resume)
            )
            if not name_matches:
                continue

            create_timestamp = (thread.get("thread_metadata") or {}).get(
                "create_timestamp"
            )
            if not create_timestamp:
                continue

            delta = abs(
                (parse_timestamp(create_timestamp) - posted_at).total_seconds()
            )
            if delta < best_score:
                best_score = delta
                best_thread_id = thread["id"]

        if best_thread_id and best_score <= 300:
            thread_by_message[message_id] = best_thread_id
            db.update_resume_thread_id(message_id, best_thread_id)
            logger.info(
                "Matched thread %s to resume %s via filename/timestamp",
                best_thread_id,
                message_id,
            )

    logger.info("Linked %s/%s resumes to critique threads", len(thread_by_message), len(resumes))
    return thread_by_message


async def scrape_thread_critiques(
    client: DiscordClient,
    thread_by_message: dict[str, str],
) -> int:
    critiques_added = 0
    scraped_threads: set[str] = set()
    total = len(thread_by_message)

    for index, (resume_message_id, thread_id) in enumerate(thread_by_message.items(), start=1):
        if not thread_id or thread_id in scraped_threads:
            continue

        scraped_threads.add(thread_id)
        added = 0

        async for message in client.iter_channel_messages(thread_id):
            if await try_insert_critique(message, resume_message_id):
                added += 1

        critiques_added += added
        logger.info(
            "Thread %s/%s: %s critiques for resume %s",
            index,
            total,
            added,
            resume_message_id,
        )

    return critiques_added


async def run_scrape(
    client: DiscordClient,
    channel_id: str,
    *,
    limit: int | None = None,
    skip_download: bool = False,
) -> tuple[int, int]:
    db.init_db()

    user = await client.get_current_user()
    logger.info(
        "Scraping as %s (%s)",
        user.get("global_name") or user.get("username"),
        user["id"],
    )

    if skip_download:
        logger.info("Skipping resume downloads")
        resumes_added = 0
        backfilled = await backfill_resume_messages(client, channel_id)
        if backfilled:
            logger.info("Backfilled post message text for %s resumes", backfilled)
    else:
        if limit is not None:
            logger.info("Resume download limit: %s new resumes this run", limit)
        resumes_added = await download_new_resumes(client, channel_id, limit=limit)

    logger.info("Scanning channel for inline reply critiques...")
    reply_critiques = await scan_channel_replies(client, channel_id)
    logger.info("Found %s inline reply critiques", reply_critiques)

    thread_by_message = await map_threads_to_resumes(client, channel_id)

    logger.info("Scraping critiques from %s threads...", len(thread_by_message))
    thread_critiques = await scrape_thread_critiques(client, thread_by_message)
    critiques_added = reply_critiques + thread_critiques

    logger.info(
        "Scrape finished: %s resumes added, %s critiques added "
        "(%s channel replies, %s thread replies)",
        resumes_added,
        critiques_added,
        reply_critiques,
        thread_critiques,
    )
    return resumes_added, critiques_added
