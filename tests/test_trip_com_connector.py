from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.connectors.base import RawFlightOffer
from app.connectors.browser_tools import BrowserCard
from app.connectors.trip_com import TripComConnector
from app.schemas import SearchCreateRequest


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        enable_browser_connectors=True,
        trip_com_result_selector="article",
        trip_com_link_selector="a[href]",
        trip_com_wait_ms=1,
        trip_com_max_cards=1,
        browser_headless=True,
        request_timeout_seconds=1,
    )


def _query(stop_preference: str) -> SearchCreateRequest:
    return SearchCreateRequest(
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
        stop_preference=stop_preference,
        sources=["trip_com"],
    )


def _dummy_offer() -> RawFlightOffer:
    return RawFlightOffer(
        source="trip_com",
        airline="AirAsia",
        flight_numbers=["AK611"],
        origin="KUL",
        destination="BKK",
        departure_at=datetime(2026, 3, 20, 1, 0, tzinfo=UTC),
        arrival_at=datetime(2026, 3, 20, 3, 30, tzinfo=UTC),
        stops=0,
        duration_minutes=150,
        cabin="economy",
        fare_brand=None,
        baggage=None,
        fare_rules=None,
        base_price=Decimal("199.00"),
        taxes=None,
        fees=None,
        total_price=Decimal("199.00"),
        currency="MYR",
        booking_url="https://trip.com",
        raw_payload={},
    )


@pytest.mark.asyncio
async def test_search_uses_pre_collect_script_when_not_non_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connector = TripComConnector(settings=_settings())
    captured: dict[str, str | None] = {"script": None}

    async def _fake_scrape_cards(**kwargs):
        captured["script"] = kwargs.get("pre_collect_script")
        return [BrowserCard(text="dummy", link="https://trip.com")]

    monkeypatch.setattr("app.connectors.trip_com.scrape_cards", _fake_scrape_cards)
    monkeypatch.setattr(TripComConnector, "_parse_card", lambda *args: _dummy_offer())

    offers = await connector.search(_query("any"))

    assert len(offers) == 1
    assert captured["script"]


@pytest.mark.asyncio
async def test_search_skips_pre_collect_script_for_non_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connector = TripComConnector(settings=_settings())
    captured: dict[str, str | None] = {"script": "not-set"}

    async def _fake_scrape_cards(**kwargs):
        captured["script"] = kwargs.get("pre_collect_script")
        return [BrowserCard(text="dummy", link="https://trip.com")]

    monkeypatch.setattr("app.connectors.trip_com.scrape_cards", _fake_scrape_cards)
    monkeypatch.setattr(TripComConnector, "_parse_card", lambda *args: _dummy_offer())

    offers = await connector.search(_query("non_stop"))

    assert len(offers) == 1
    assert captured["script"] is None
