from __future__ import annotations

from urllib.parse import urlparse

import httpx


class LinkValidator:
    def __init__(self, timeout_seconds: int = 10) -> None:
        self._client = httpx.AsyncClient(
            timeout=timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": "flight-search-validator/1.0"},
        )

    async def validate(self, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return False

        try:
            response = await self._client.head(url)
            if response.status_code < 400:
                return True
            response = await self._client.get(url)
            return response.status_code < 400
        except Exception:
            return False

    async def close(self) -> None:
        await self._client.aclose()

