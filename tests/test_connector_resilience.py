from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from app.connectors.airasia import AirAsiaConnector, _ResolvedStation
from app.connectors.base import FlightConnector, RawFlightOffer
from app.schemas import SearchCreateRequest
from app.services.orchestrator import _run_single


class _FailingConnector(FlightConnector):
    name = "failing"

    async def search(self, query: SearchCreateRequest) -> list[RawFlightOffer]:
        raise AssertionError()


class _DummyAsyncClient:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_run_single_error_message_has_exception_type() -> None:
    query = SearchCreateRequest(
        origin="KUL",
        destination="BKK",
        departure_date="2026-03-20",
        return_date=None,
        trip_type="one_way",
        adults=1,
        children=0,
        infants=0,
        cabin="economy",
        currency="MYR",
        sources=["trip_com"],
    )
    connector = _FailingConnector(settings=SimpleNamespace())

    result = await _run_single(connector, query, timeout_seconds=5, retries=0)

    assert result.status == "error"
    assert result.error_message == "AssertionError: no details provided"


@pytest.mark.asyncio
async def test_airasia_returns_offer_when_deeplink_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = SimpleNamespace(
        request_timeout_seconds=10,
        enable_browser_connectors=False,
        browser_headless=True,
        airasia_wait_ms=3500,
    )
    connector = AirAsiaConnector(settings=settings)
    query = SearchCreateRequest(
        origin="KUL",
        destination="BKK",
        departure_date="2026-03-20",
        return_date=None,
        trip_type="one_way",
        adults=1,
        children=0,
        infants=0,
        cabin="economy",
        currency="MYR",
        sources=["airasia"],
    )

    async def _resolve(*args, **kwargs):
        query_text = str(args[2]).upper()
        return _ResolvedStation(station_code=query_text)

    async def _auth(*args, **kwargs):
        return "jwt-token"

    async def _lowfare(*args, **kwargs):
        return {"price": "189.90", "airlineProfile": "Value Pack"}

    async def _deeplink(*args, **kwargs):
        raise RuntimeError("deeplink endpoint unavailable")

    monkeypatch.setattr(httpx, "AsyncClient", _DummyAsyncClient)
    monkeypatch.setattr(AirAsiaConnector, "_resolve_station", _resolve)
    monkeypatch.setattr(AirAsiaConnector, "_fetch_auth_token", _auth)
    monkeypatch.setattr(AirAsiaConnector, "_fetch_lowfare_item", _lowfare)
    monkeypatch.setattr(AirAsiaConnector, "_fetch_deeplink_url", _deeplink)

    offers = await connector.search(query)

    assert len(offers) == 1
    offer = offers[0]
    assert offer.total_price == Decimal("189.90")
    assert offer.booking_url.startswith("https://www.airasia.com/en/gb?")
    assert "origin=KUL" in offer.booking_url
    assert "destination=BKK" in offer.booking_url
    assert offer.raw_payload.get("deeplink_error") == "RuntimeError: deeplink endpoint unavailable"
    assert "fallback search link used" in (offer.fare_rules or "").lower()
