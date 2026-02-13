from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models import ConnectorRun, Offer, SearchRequest
from app.schemas import (
    ConnectorFailureOut,
    ConnectorHealthItem,
    ConnectorHealthResponse,
    ConnectorRunOut,
    FlightOfferDetailOut,
    FlightOfferOut,
    SearchCreateRequest,
    SearchCreateResponse,
    SearchResultResponse,
    SearchStatus,
)
from app.services.query_hash import build_query_hash
from app.workers.search_worker import SearchWorker

router = APIRouter()
_MALAYSIA_TZ = ZoneInfo("Asia/Kuala_Lumpur")
_MAX_ALTERNATIVES = 5


def _ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _offer_datetime_to_utc(value: datetime, raw_payload: dict | None) -> datetime:
    if value.tzinfo is not None:
        return value
    marker = (raw_payload or {}).get("_stored_timezone")
    if marker == "utc":
        return value.replace(tzinfo=UTC)
    # Legacy SQLite rows were saved as naive local wall-time; interpret as Malaysia time.
    return value.replace(tzinfo=_MALAYSIA_TZ).astimezone(UTC)


def _offer_to_out(offer: Offer) -> FlightOfferOut:
    return FlightOfferOut(
        offer_id=offer.id,
        source=offer.source,
        airline=offer.airline,
        flight_numbers=offer.flight_numbers,
        departure_at=_offer_datetime_to_utc(offer.departure_at, offer.raw_payload),
        arrival_at=_offer_datetime_to_utc(offer.arrival_at, offer.raw_payload),
        stops=offer.stops,
        duration_minutes=offer.duration_minutes,
        cabin=offer.cabin,
        baggage=offer.baggage,
        fare_rules=offer.fare_rules,
        total_price=offer.total_price,
        currency=offer.currency,
        booking_url=offer.booking_url,
        deep_link_valid=offer.deep_link_valid,
    )


def _offer_to_detail_out(offer: Offer) -> FlightOfferDetailOut:
    return FlightOfferDetailOut(
        offer_id=offer.id,
        source=offer.source,
        airline=offer.airline,
        flight_numbers=offer.flight_numbers,
        departure_at=_offer_datetime_to_utc(offer.departure_at, offer.raw_payload),
        arrival_at=_offer_datetime_to_utc(offer.arrival_at, offer.raw_payload),
        stops=offer.stops,
        duration_minutes=offer.duration_minutes,
        cabin=offer.cabin,
        baggage=offer.baggage,
        fare_rules=offer.fare_rules,
        total_price=offer.total_price,
        currency=offer.currency,
        booking_url=offer.booking_url,
        deep_link_valid=offer.deep_link_valid,
        fare_brand=offer.fare_brand,
        base_price=offer.base_price,
        taxes=offer.taxes,
        fees=offer.fees,
        raw_payload=offer.raw_payload,
    )


def _select_alternative_offers(offers: list[Offer], limit: int) -> list[Offer]:
    if limit <= 0 or len(offers) <= 1:
        return []

    selected: list[Offer] = []
    selected_ids: set[str] = set()
    seen_airlines: set[str] = set()

    # First pass: maximize airline variety so one carrier does not dominate the list.
    for offer in offers[1:]:
        airline_key = (offer.airline or "").strip().lower()
        if airline_key in seen_airlines:
            continue
        selected.append(offer)
        selected_ids.add(offer.id)
        seen_airlines.add(airline_key)
        if len(selected) >= limit:
            return selected

    # Second pass: fill the remaining slots with cheapest leftovers.
    for offer in offers[1:]:
        if offer.id in selected_ids:
            continue
        selected.append(offer)
        selected_ids.add(offer.id)
        if len(selected) >= limit:
            break

    return selected


@router.post("/search", response_model=SearchCreateResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_search(
    payload: SearchCreateRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> SearchCreateResponse:
    settings = get_settings()
    requested_sources = payload.sources if payload.sources else settings.default_sources
    payload = payload.model_copy(update={"sources": requested_sources})

    search = SearchRequest(
        query_hash=build_query_hash(payload),
        query_json=payload.model_dump(mode="json"),
        status=SearchStatus.queued.value,
    )
    session.add(search)
    await session.commit()
    await session.refresh(search)

    worker: SearchWorker = request.app.state.search_worker
    worker.launch(search.id)

    return SearchCreateResponse(
        search_id=search.id,
        status=SearchStatus(search.status),
        created_at=_ensure_utc(search.created_at),
    )


@router.get("/search/{search_id}", response_model=SearchResultResponse)
async def get_search(
    search_id: str,
    session: AsyncSession = Depends(get_session),
) -> SearchResultResponse:
    search = await session.get(SearchRequest, search_id)
    if not search:
        raise HTTPException(status_code=404, detail="search_id not found")

    query = SearchCreateRequest.model_validate(search.query_json)

    offers_stmt = (
        select(Offer)
        .where(Offer.search_id == search_id)
        .order_by(Offer.total_price.asc(), Offer.stops.asc(), Offer.duration_minutes.asc())
    )
    offers = list((await session.execute(offers_stmt)).scalars().all())
    cheapest = _offer_to_out(offers[0]) if offers else None
    settings = get_settings()
    alternative_limit = min(max(settings.max_offers_per_search - 1, 0), _MAX_ALTERNATIVES)
    alternative_offers = _select_alternative_offers(offers, alternative_limit)
    alternatives = [_offer_to_out(item) for item in alternative_offers]

    runs_stmt = (
        select(ConnectorRun)
        .where(ConnectorRun.search_id == search_id)
        .order_by(ConnectorRun.created_at.asc(), ConnectorRun.source.asc())
    )
    runs = list((await session.execute(runs_stmt)).scalars().all())
    source_sequence = query.sources or get_settings().default_sources
    requested_order = {source: index for index, source in enumerate(source_sequence)}
    runs.sort(
        key=lambda run: (
            requested_order.get(run.source, len(requested_order)),
            run.created_at,
        )
    )
    offer_count_by_source = Counter(item.source for item in offers)
    connector_runs = [
        ConnectorRunOut(
            source=run.source,
            status=run.status,
            latency_ms=run.latency_ms,
            error_message=run.error_message,
            offer_count=offer_count_by_source.get(run.source, 0),
        )
        for run in runs
    ]
    failures = [
        ConnectorFailureOut(
            source=run.source,
            status=run.status,
            message=run.error_message or "Connector failed",
        )
        for run in connector_runs
        if run.status != "success"
    ]

    return SearchResultResponse(
        search_id=search.id,
        status=SearchStatus(search.status),
        query=query,
        cheapest_flight=cheapest,
        alternatives=alternatives,
        price_last_checked_at=_ensure_utc(search.completed_at),
        failures=failures,
        connector_runs=connector_runs,
    )


@router.get("/search/{search_id}/offers/{offer_id}", response_model=FlightOfferDetailOut)
async def get_search_offer(
    search_id: str,
    offer_id: str,
    session: AsyncSession = Depends(get_session),
) -> FlightOfferDetailOut:
    offer_stmt = select(Offer).where(Offer.search_id == search_id, Offer.id == offer_id)
    offer = (await session.execute(offer_stmt)).scalar_one_or_none()
    if offer:
        return _offer_to_detail_out(offer)

    search = await session.get(SearchRequest, search_id)
    if not search:
        raise HTTPException(status_code=404, detail="search_id not found")
    raise HTTPException(status_code=404, detail="offer_id not found for search_id")


@router.get("/health/connectors", response_model=ConnectorHealthResponse)
async def connector_health(
    session: AsyncSession = Depends(get_session),
) -> ConnectorHealthResponse:
    settings = get_settings()
    known_sources = set(settings.default_sources)

    runs_stmt = select(ConnectorRun).order_by(ConnectorRun.created_at.desc())
    runs = list((await session.execute(runs_stmt)).scalars().all())
    latest_by_source: dict[str, ConnectorRun] = {}
    for run in runs:
        known_sources.add(run.source)
        if run.source not in latest_by_source:
            latest_by_source[run.source] = run

    connectors: list[ConnectorHealthItem] = []
    for source in sorted(known_sources):
        run = latest_by_source.get(source)
        if run is None:
            connectors.append(
                ConnectorHealthItem(
                    source=source,
                    status="never_run",
                    last_latency_ms=None,
                    last_error=None,
                    last_checked_at=None,
                )
            )
            continue

        connectors.append(
            ConnectorHealthItem(
                source=source,
                status=run.status,
                last_latency_ms=run.latency_ms,
                last_error=run.error_message,
                last_checked_at=_ensure_utc(run.created_at),
            )
        )

    return ConnectorHealthResponse(connectors=connectors)
