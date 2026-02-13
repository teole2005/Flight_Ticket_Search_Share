from datetime import UTC, datetime
from decimal import Decimal

from app.connectors.base import RawFlightOffer
from app.schemas import StopPreference
from app.services.stops import filter_offers_by_stops


def _offer(stops: int) -> RawFlightOffer:
    return RawFlightOffer(
        source="trip_com",
        airline="AirAsia",
        flight_numbers=["AK611"],
        origin="KUL",
        destination="BKK",
        departure_at=datetime(2026, 3, 20, 9, 0, tzinfo=UTC),
        arrival_at=datetime(2026, 3, 20, 11, 30, tzinfo=UTC),
        stops=stops,
        duration_minutes=150,
        cabin="economy",
        fare_brand=None,
        baggage=None,
        fare_rules=None,
        base_price=Decimal("200.00"),
        taxes=None,
        fees=None,
        total_price=Decimal("200.00"),
        currency="MYR",
        booking_url="https://example.com",
        raw_payload={},
    )


def test_filter_offers_non_stop_only() -> None:
    offers = [_offer(0), _offer(1), _offer(2)]
    filtered = filter_offers_by_stops(offers, StopPreference.non_stop)
    assert [item.stops for item in filtered] == [0]


def test_filter_offers_multiple_stops_only() -> None:
    offers = [_offer(0), _offer(1), _offer(2)]
    filtered = filter_offers_by_stops(offers, StopPreference.multiple_stops)
    assert [item.stops for item in filtered] == [1, 2]


def test_filter_offers_any_keeps_all() -> None:
    offers = [_offer(0), _offer(1)]
    filtered = filter_offers_by_stops(offers, StopPreference.any)
    assert [item.stops for item in filtered] == [0, 1]
