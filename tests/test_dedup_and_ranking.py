from datetime import UTC, datetime
from decimal import Decimal

from app.connectors.base import RawFlightOffer
from app.services.dedup import deduplicate_offers
from app.services.ranking import rank_offers


def _offer(price: str, duration: int, stops: int, booking_url: str) -> RawFlightOffer:
    return RawFlightOffer(
        source="trip_com",
        airline="AirAsia",
        flight_numbers=["AK611"],
        origin="KUL",
        destination="BKK",
        departure_at=datetime(2026, 3, 20, 9, 0, tzinfo=UTC),
        arrival_at=datetime(2026, 3, 20, 11, 30, tzinfo=UTC),
        stops=stops,
        duration_minutes=duration,
        cabin="economy",
        fare_brand=None,
        baggage=None,
        fare_rules=None,
        base_price=Decimal(price),
        taxes=None,
        fees=None,
        total_price=Decimal(price),
        currency="MYR",
        booking_url=booking_url,
        raw_payload={},
    )


def test_deduplicate_keeps_cheapest_offer() -> None:
    offers = [
        _offer("320.00", duration=150, stops=0, booking_url="https://a.example"),
        _offer("289.50", duration=150, stops=0, booking_url="https://b.example"),
    ]
    deduped = deduplicate_offers(offers)
    assert len(deduped) == 1
    assert deduped[0].total_price == Decimal("289.50")


def test_rank_orders_price_then_stops_then_duration() -> None:
    offer_a = _offer("280.00", duration=180, stops=1, booking_url="https://a.example")
    offer_b = _offer("280.00", duration=170, stops=0, booking_url="https://b.example")
    offer_c = _offer("300.00", duration=120, stops=0, booking_url="https://c.example")
    ranked = rank_offers([offer_c, offer_a, offer_b])
    assert ranked[0].booking_url == "https://b.example"
    assert ranked[1].booking_url == "https://a.example"
    assert ranked[2].booking_url == "https://c.example"
