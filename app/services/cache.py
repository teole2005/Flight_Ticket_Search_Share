from __future__ import annotations

import json
import logging
from typing import Any

from redis.asyncio import Redis

logger = logging.getLogger(__name__)


class CacheClient:
    def __init__(self, redis_url: str | None) -> None:
        self._redis_url = redis_url
        self._redis: Redis | None = None

    async def connect(self) -> None:
        if not self._redis_url:
            return
        client = Redis.from_url(self._redis_url, decode_responses=True)
        try:
            await client.ping()
            self._redis = client
            logger.info("Redis cache connected")
        except Exception as exc:
            logger.warning("Redis unavailable; continuing without cache: %s", exc)
            await client.aclose()
            self._redis = None

    async def get_json(self, key: str) -> dict[str, Any] | None:
        if not self._redis:
            return None
        value = await self._redis.get(key)
        if not value:
            return None
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            logger.warning("Invalid cached payload at key=%s", key)
            return None

    async def set_json(self, key: str, payload: dict[str, Any], ttl_seconds: int) -> None:
        if not self._redis:
            return
        await self._redis.set(key, json.dumps(payload), ex=ttl_seconds)

    async def close(self) -> None:
        if self._redis:
            await self._redis.aclose()
            self._redis = None
