from __future__ import annotations

from app.connectors.base import RawFlightOffer
from app.schemas import StopPreference


def filter_offers_by_stops(
    offers: list[RawFlightOffer],
    stop_preference: StopPreference,
) -> list[RawFlightOffer]:
    if stop_preference == StopPreference.non_stop:
        return [offer for offer in offers if offer.stops == 0]
    if stop_preference == StopPreference.multiple_stops:
        return [offer for offer in offers if offer.stops >= 1]
    return offers
