import argparse
import asyncio
import logging
import sys

from bot.client import DiscordClient
from bot.config import DISCORD_USER_TOKEN, EXPORT_DIR, RESUME_CHANNEL_ID
from bot.export import run_export
from bot.scraper import repair_missing_attachments, run_scrape

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def scrape_command(
    limit: int | None = None,
    skip_download: bool = False,
) -> int:
    if not DISCORD_USER_TOKEN:
        logger.error("DISCORD_USER_TOKEN is not set. Copy .env.example to .env and fill it in.")
        return 1

    if not RESUME_CHANNEL_ID:
        logger.error("RESUME_CHANNEL_ID is not set.")
        return 1

    async with DiscordClient(DISCORD_USER_TOKEN) as client:
        resumes_added, critiques_added = await run_scrape(
            client,
            RESUME_CHANNEL_ID,
            limit=limit,
            skip_download=skip_download,
        )

    print(f"Scrape complete: {resumes_added} resumes added, {critiques_added} critiques added.")
    return 0


async def repair_command() -> int:
    if not DISCORD_USER_TOKEN or not RESUME_CHANNEL_ID:
        logger.error("DISCORD_USER_TOKEN / RESUME_CHANNEL_ID not set.")
        return 1

    async with DiscordClient(DISCORD_USER_TOKEN) as client:
        repaired = await repair_missing_attachments(client, RESUME_CHANNEL_ID, EXPORT_DIR)

    print(f"Repair complete: {repaired} resumes had attachments re-downloaded.")
    print("Run `export` again to refresh dataset.json and the export folders.")
    return 0


def export_command() -> int:
    resume_count, critique_count, dataset_path = run_export()
    print(
        f"Export complete: {resume_count} resumes, {critique_count} critiques.\n"
        f"Dataset written to: {dataset_path}"
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scrape resume posts and critiques from a Discord channel using your account."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    scrape_parser = subparsers.add_parser("scrape", help="Scrape the configured resume channel")
    scrape_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max number of new resumes to download this run (skips already saved resumes)",
    )
    scrape_parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Only fetch critiques and thread links; do not download resume files",
    )

    subparsers.add_parser("export", help="Export scraped data to data/export/dataset.json")
    subparsers.add_parser(
        "repair",
        help="Re-download resume attachments recorded in the DB but missing on disk",
    )

    args = parser.parse_args()

    if args.command == "scrape":
        raise SystemExit(
            asyncio.run(scrape_command(limit=args.limit, skip_download=args.skip_download))
        )

    if args.command == "export":
        raise SystemExit(export_command())

    if args.command == "repair":
        raise SystemExit(asyncio.run(repair_command()))

    raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
