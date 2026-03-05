from __future__ import annotations

import logging
import time
from decimal import ROUND_HALF_UP, Decimal

import httpx

logger = logging.getLogger(__name__)


class FxService:
    def __init__(self, ttl_seconds: int = 1800, timeout_seconds: int = 10) -> None:
        self._ttl_seconds = ttl_seconds
        self._cache: dict[tuple[str, str], tuple[Decimal, float]] = {}
        self._client = httpx.AsyncClient(timeout=timeout_seconds)
        self._fallback_rates = {
            "USD": Decimal("4.70"),
            "EUR": Decimal("5.10"),
            "SGD": Decimal("3.50"),
            "GBP": Decimal("6.00"),
            "AUD": Decimal("3.10"),
            "JPY": Decimal("0.031"),
            "THB": Decimal("0.13"),
            "IDR": Decimal("0.0003"),
            "MYR": Decimal("1.0"),
        }

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

        try:
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
        except httpx.HTTPError as e:
            logger.warning(f"FX API error ({source}->{target}): {e}. Using fallback rates.")
            # Calculate fallback rate using MYR as base if possible
            if source in self._fallback_rates and target in self._fallback_rates:
                # rate = target_value / source_value based on MYR base
                # In this dictionary, the values are X to MYR
                # So source_to_myr = self._fallback_rates[source]
                # target_to_myr = self._fallback_rates[target]
                # Then source_to_target = source_to_myr / target_to_myr
                source_to_myr = self._fallback_rates[source]
                target_to_myr = self._fallback_rates[target]
                fallback_rate = (source_to_myr / target_to_myr).quantize(Decimal("0.0001"))
                logger.info(f"Using fallback rate {fallback_rate} for {source}->{target}")
                return fallback_rate
                
            raise ValueError(f"FX rate unavailable from {source} to {target} (API failed and no fallback)") from e

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

