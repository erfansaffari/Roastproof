import asyncio
import logging
import ssl
from collections.abc import AsyncIterator
from typing import Any

import aiohttp
import certifi

from bot.config import RATE_LIMIT_SLEEP

logger = logging.getLogger(__name__)


class DiscordClient:
    BASE_URL = "https://discord.com/api/v10"

    def __init__(self, token: str, rate_limit_sleep: float = RATE_LIMIT_SLEEP) -> None:
        self._token = token
        self._rate_limit_sleep = rate_limit_sleep
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> "DiscordClient":
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        self._session = aiohttp.ClientSession(
            connector=connector,
            headers={
                "Authorization": self._token,
                "Content-Type": "application/json",
                "User-Agent": "resdumper (personal scraper)",
            },
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._session is not None:
            await self._session.close()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        optional: bool = False,
    ) -> Any:
        if self._session is None:
            raise RuntimeError("DiscordClient must be used as an async context manager.")

        url = f"{self.BASE_URL}{path}"

        while True:
            async with self._session.request(method, url, params=params) as response:
                if response.status == 429:
                    retry_after = float(response.headers.get("Retry-After", "1"))
                    try:
                        payload = await response.json()
                        retry_after = float(payload.get("retry_after", retry_after))
                    except aiohttp.ContentTypeError:
                        pass
                    logger.warning("Rate limited, sleeping %.1fs", retry_after)
                    await asyncio.sleep(retry_after + 0.1)
                    continue

                if response.status >= 400:
                    body = await response.text()
                    if optional:
                        logger.warning(
                            "Skipping optional Discord API call %s (%s): %s",
                            path,
                            response.status,
                            body,
                        )
                        await asyncio.sleep(self._rate_limit_sleep)
                        return None
                    raise RuntimeError(
                        f"Discord API error {response.status} for {path}: {body}"
                    )

                if response.status == 204:
                    await asyncio.sleep(self._rate_limit_sleep)
                    return None

                data = await response.json()
                await asyncio.sleep(self._rate_limit_sleep)
                return data

    async def get_current_user(self) -> dict[str, Any]:
        return await self._request("GET", "/users/@me")

    async def get_message(self, channel_id: str, message_id: str) -> dict[str, Any] | None:
        return await self._request(
            "GET",
            f"/channels/{channel_id}/messages/{message_id}",
            optional=True,
        )

    async def iter_channel_messages(self, channel_id: str) -> AsyncIterator[dict[str, Any]]:
        before: str | None = None

        while True:
            params: dict[str, Any] = {"limit": 100}
            if before is not None:
                params["before"] = before

            batch = await self._request(
                "GET",
                f"/channels/{channel_id}/messages",
                params=params,
            )

            if not batch:
                break

            for message in batch:
                yield message

            before = batch[-1]["id"]

    async def _iter_user_threads(
        self,
        path: str,
        *,
        before_key: str = "before",
    ) -> list[dict[str, Any]]:
        threads: list[dict[str, Any]] = []
        before: str | None = None

        while True:
            params: dict[str, Any] = {"limit": 100}
            if before is not None:
                params[before_key] = before

            data = await self._request("GET", path, params=params, optional=True)
            if not data:
                break

            batch = data.get("threads", [])
            threads.extend(batch)

            if not data.get("has_more") or not batch:
                break

            if before_key == "before" and "thread_metadata" in batch[-1]:
                before = batch[-1]["thread_metadata"]["archive_timestamp"]
            else:
                before = batch[-1]["id"]

        return threads

    async def get_joined_active_threads(self) -> list[dict[str, Any]]:
        return await self._iter_user_threads("/users/@me/threads")

    async def get_joined_archived_public_threads(self) -> list[dict[str, Any]]:
        return await self._iter_user_threads("/users/@me/threads/archived/public")

    async def get_channel_archived_threads(self, channel_id: str) -> list[dict[str, Any]]:
        threads: list[dict[str, Any]] = []
        before: str | None = None

        while True:
            params: dict[str, Any] = {"limit": 100}
            if before is not None:
                params["before"] = before

            data = await self._request(
                "GET",
                f"/channels/{channel_id}/threads/archived/public",
                params=params,
                optional=True,
            )
            if not data:
                break

            batch = data.get("threads", [])
            threads.extend(batch)

            if not data.get("has_more") or not batch:
                break

            before = batch[-1]["thread_metadata"]["archive_timestamp"]

        return threads

    async def download_url(self, url: str, dest_path: str) -> None:
        if self._session is None:
            raise RuntimeError("DiscordClient must be used as an async context manager.")

        async with self._session.get(url) as response:
            response.raise_for_status()
            with open(dest_path, "wb") as file:
                async for chunk in response.content.iter_chunked(8192):
                    file.write(chunk)

        await asyncio.sleep(self._rate_limit_sleep)
