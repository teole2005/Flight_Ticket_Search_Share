from __future__ import annotations

from dataclasses import replace

from app.connectors.base import RawFlightOffer
from app.services.fx import FxService


async def normalize_offer(
    offer: RawFlightOffer, target_currency: str, fx_service: FxService
) -> RawFlightOffer:
    currency = target_currency.upper()
    normalized = replace(
        offer,
        source=offer.source.lower(),
        origin=offer.origin.upper(),
        destination=offer.destination.upper(),
    )

    if normalized.currency.upper() == currency:
        normalized.currency = currency
        return normalized

    normalized = replace(
        normalized,
        base_price=await fx_service.convert(normalized.base_price, normalized.currency, currency),
        taxes=await fx_service.convert(normalized.taxes, normalized.currency, currency),
        fees=await fx_service.convert(normalized.fees, normalized.currency, currency),
        total_price=await fx_service.convert(
            normalized.total_price,
            normalized.currency,
            currency,
        ),
        currency=currency,
    )
    return normalized


async def normalize_offers(
    offers: list[RawFlightOffer], target_currency: str, fx_service: FxService
) -> list[RawFlightOffer]:
    normalized: list[RawFlightOffer] = []
    for offer in offers:
        item = await normalize_offer(offer, target_currency, fx_service)
        normalized.append(item)
    return normalized
