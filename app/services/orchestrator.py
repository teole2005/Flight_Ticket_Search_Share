from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from app.connectors.base import FlightConnector, RawFlightOffer
from app.schemas import SearchCreateRequest


@dataclass(slots=True)
class ConnectorRunResult:
    source: str
    status: str
    latency_ms: int
    offers: list[RawFlightOffer]
    error_message: str | None = None


async def execute_connectors(
    query: SearchCreateRequest,
    connectors: list[FlightConnector],
    timeout_seconds: int,
    retries: int,
    max_parallel: int,
) -> list[ConnectorRunResult]:
    semaphore = asyncio.Semaphore(max_parallel)

    async def _guarded(connector: FlightConnector) -> ConnectorRunResult:
        async with semaphore:
            return await _run_single(connector, query, timeout_seconds, retries)

    return await asyncio.gather(*[_guarded(connector) for connector in connectors])


async def _run_single(
    connector: FlightConnector,
    query: SearchCreateRequest,
    timeout_seconds: int,
    retries: int,
) -> ConnectorRunResult:
    started = time.perf_counter()
    last_error: str | None = None
    status = "error"

    for _ in range(retries + 1):
        try:
            offers = await asyncio.wait_for(connector.search(query), timeout=timeout_seconds)
            latency_ms = int((time.perf_counter() - started) * 1000)
            return ConnectorRunResult(
                source=connector.name,
                status="success",
                latency_ms=latency_ms,
                offers=offers,
                error_message=None,
            )
        except TimeoutError:
            status = "timeout"
            last_error = f"Timed out after {timeout_seconds}s"
        except Exception as exc:
            status = "error"
            last_error = _format_exception_message(exc)

    latency_ms = int((time.perf_counter() - started) * 1000)
    return ConnectorRunResult(
        source=connector.name,
        status=status,
        latency_ms=latency_ms,
        offers=[],
        error_message=last_error,
    )


def _format_exception_message(exc: Exception) -> str:
    message = str(exc).strip()
    if message:
        return f"{type(exc).__name__}: {message}"
    return f"{type(exc).__name__}: no details provided"
