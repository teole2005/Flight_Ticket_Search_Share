from __future__ import annotations

import time
from decimal import ROUND_HALF_UP, Decimal

import httpx


class FxService:
    def __init__(self, ttl_seconds: int = 1800, timeout_seconds: int = 10) -> None:
        self._ttl_seconds = ttl_seconds
        self._cache: dict[tuple[str, str], tuple[Decimal, float]] = {}
        self._client = httpx.AsyncClient(timeout=timeout_seconds)

    async def get_rate(self, from_currency: str, to_currency: str) -> Decimal:
        source = from_currency.upper()
        target = to_currency.upper()
        if source == target:
            return Decimal("1")

        key = (source, target)
        now = time.monotonic()
        cached = self._cache.get(key)
        if cached and cached[1] > now:
            return cached[0]

        response = await self._client.get(
            "https://api.frankfurter.app/latest",
            params={"from": source, "to": target},
        )
        response.raise_for_status()
        payload = response.json()
        rate_value = payload.get("rates", {}).get(target)
        if rate_value is None:
            raise ValueError(f"FX rate unavailable from {source} to {target}")

        rate = Decimal(str(rate_value))
        self._cache[key] = (rate, now + self._ttl_seconds)
        return rate

    async def convert(
        self, amount: Decimal | None, from_currency: str, to_currency: str
    ) -> Decimal | None:
        if amount is None:
            return None
        source = from_currency.upper()
        target = to_currency.upper()
        if source == target:
            return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        rate = await self.get_rate(source, target)
        converted = amount * rate
        return converted.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    async def close(self) -> None:
        await self._client.aclose()

