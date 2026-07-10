from pathlib import Path
from typing import Any

from bot.client import DiscordClient

SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".pdf"}


def is_supported_attachment(attachment: dict[str, Any]) -> bool:
    filename = attachment.get("filename")
    if not filename:
        return False
    return Path(filename).suffix.lower() in SUPPORTED_EXTENSIONS


def filter_supported_attachments(
    attachments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [attachment for attachment in attachments if is_supported_attachment(attachment)]


async def download_attachments(
    client: DiscordClient,
    attachments: list[dict[str, Any]],
    dest_dir: Path,
) -> list[str]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    saved_paths: list[str] = []

    for attachment in attachments:
        filename = attachment["filename"]
        dest_path = dest_dir / filename

        if dest_path.exists() and dest_path.stat().st_size > 0:
            saved_paths.append(str(dest_path))
            continue

        await client.download_url(attachment["url"], str(dest_path))
        saved_paths.append(str(dest_path))

    return saved_paths
