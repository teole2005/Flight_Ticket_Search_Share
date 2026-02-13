from __future__ import annotations

from app.connectors.base import RawFlightOffer


def rank_offers(offers: list[RawFlightOffer]) -> list[RawFlightOffer]:
    return sorted(
        offers,
        key=lambda item: (
            item.total_price,
            item.stops,
            item.duration_minutes,
        ),
    )

