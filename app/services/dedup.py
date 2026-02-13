from __future__ import annotations

import hashlib

from app.connectors.base import RawFlightOffer


def build_dedup_key(offer: RawFlightOffer) -> str:
    payload = "|".join(
        [
            offer.origin.upper(),
            offer.destination.upper(),
            offer.departure_at.isoformat(timespec="minutes"),
            offer.arrival_at.isoformat(timespec="minutes"),
            offer.airline.upper(),
            ",".join(sorted(number.upper() for number in offer.flight_numbers)),
            (offer.cabin or "").upper(),
        ]
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def deduplicate_offers(offers: list[RawFlightOffer]) -> list[RawFlightOffer]:
    selected: dict[str, RawFlightOffer] = {}
    for offer in offers:
        dedup_key = build_dedup_key(offer)
        current = selected.get(dedup_key)
        if current is None:
            selected[dedup_key] = offer
            continue
        if offer.total_price < current.total_price:
            selected[dedup_key] = offer
            continue
        if (
            offer.total_price == current.total_price
            and offer.duration_minutes < current.duration_minutes
        ):
            selected[dedup_key] = offer
    return list(selected.values())
