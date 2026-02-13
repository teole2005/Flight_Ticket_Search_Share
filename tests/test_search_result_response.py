from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.api.routes import get_search, get_search_offer
from app.db import Base
from app.models import ConnectorRun, Offer, SearchRequest
from app.schemas import SearchCreateRequest, SearchStatus


def _query_payload() -> dict:
    query = SearchCreateRequest(
        origin="KUL",
        destination="BKK",
        departure_date="2026-03-20",
        return_date="2026-03-25",
        trip_type="round_trip",
        adults=1,
        children=0,
        infants=0,
        cabin="economy",
        currency="MYR",
        sources=["trip_com", "airasia"],
    )
    return query.model_dump(mode="json")


def _query_payload_with_sources(sources: list[str]) -> dict:
    query = SearchCreateRequest(
        origin="KUL",
        destination="BKK",
        departure_date="2026-03-20",
        return_date="2026-03-25",
        trip_type="round_trip",
        adults=1,
        children=0,
        infants=0,
        cabin="economy",
        currency="MYR",
        sources=sources,
    )
    return query.model_dump(mode="json")


def _build_offer(
    *,
    offer_id: str,
    search_id: str,
    source: str,
    total_price: str,
    booking_url: str,
    airline: str = "AirAsia",
    fare_brand: str | None = None,
    base_price: str | None = None,
    taxes: str | None = None,
    fees: str | None = None,
    raw_payload: dict | None = None,
) -> Offer:
    return Offer(
        id=offer_id,
        search_id=search_id,
        source=source,
        dedup_key=f"{source}-{offer_id}",
        airline=airline,
        flight_numbers=["AK611"],
        origin="KUL",
        destination="BKK",
        departure_at=datetime(2026, 3, 20, 9, 0, tzinfo=UTC),
        arrival_at=datetime(2026, 3, 20, 11, 35, tzinfo=UTC),
        stops=0,
        duration_minutes=155,
        cabin="economy",
        fare_brand=fare_brand,
        baggage="7kg carry-on",
        fare_rules="No refund",
        base_price=Decimal(base_price) if base_price is not None else Decimal(total_price),
        taxes=Decimal(taxes) if taxes is not None else None,
        fees=Decimal(fees) if fees is not None else None,
        total_price=float(total_price),
        currency="MYR",
        booking_url=booking_url,
        deep_link_valid=True,
        raw_payload=raw_payload or {},
    )


async def _create_session_factory(tmp_path) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    db_path = tmp_path / "search_result_test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    return engine, session_factory


@pytest.mark.asyncio
async def test_get_search_returns_connector_runs_with_offer_counts(tmp_path) -> None:
    engine, session_factory = await _create_session_factory(tmp_path)
    search_id = "00000000-0000-0000-0000-000000000101"
    try:
        async with session_factory() as session:
            session.add(
                SearchRequest(
                    id=search_id,
                    query_hash="hash-a",
                    query_json=_query_payload(),
                    status=SearchStatus.completed.value,
                    created_at=datetime(2026, 2, 12, 6, 0, tzinfo=UTC),
                    started_at=datetime(2026, 2, 12, 6, 0, 2, tzinfo=UTC),
                    completed_at=datetime(2026, 2, 12, 6, 0, 5, tzinfo=UTC),
                )
            )
            session.add_all(
                [
                    _build_offer(
                        offer_id="00000000-0000-0000-0000-000000000201",
                        search_id=search_id,
                        source="trip_com",
                        total_price="199.90",
                        booking_url="https://trip.com/book-a",
                    ),
                    _build_offer(
                        offer_id="00000000-0000-0000-0000-000000000202",
                        search_id=search_id,
                        source="trip_com",
                        total_price="249.90",
                        booking_url="https://trip.com/book-b",
                    ),
                ]
            )
            session.add_all(
                [
                    ConnectorRun(
                        id="00000000-0000-0000-0000-000000000301",
                        search_id=search_id,
                        source="trip_com",
                        status="success",
                        latency_ms=118,
                        error_message=None,
                    ),
                    ConnectorRun(
                        id="00000000-0000-0000-0000-000000000302",
                        search_id=search_id,
                        source="airasia",
                        status="error",
                        latency_ms=431,
                        error_message="Connector failed",
                    ),
                ]
            )
            await session.commit()

        async with session_factory() as session:
            result = await get_search(search_id=search_id, session=session)

        runs = {item.source: item for item in result.connector_runs}
        assert set(runs) == {"trip_com", "airasia"}
        assert runs["trip_com"].offer_count == 2
        assert runs["trip_com"].status == "success"
        assert runs["airasia"].offer_count == 0
        assert runs["airasia"].error_message == "Connector failed"
        assert [item.source for item in result.failures] == ["airasia"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_get_search_completed_without_offers_returns_run_diagnostics(tmp_path) -> None:
    engine, session_factory = await _create_session_factory(tmp_path)
    search_id = "00000000-0000-0000-0000-000000000102"
    try:
        async with session_factory() as session:
            session.add(
                SearchRequest(
                    id=search_id,
                    query_hash="hash-b",
                    query_json=_query_payload(),
                    status=SearchStatus.completed.value,
                    created_at=datetime(2026, 2, 12, 7, 0, tzinfo=UTC),
                    started_at=datetime(2026, 2, 12, 7, 0, 1, tzinfo=UTC),
                    completed_at=datetime(2026, 2, 12, 7, 0, 4, tzinfo=UTC),
                )
            )
            session.add_all(
                [
                    ConnectorRun(
                        id="00000000-0000-0000-0000-000000000303",
                        search_id=search_id,
                        source="trip_com",
                        status="success",
                        latency_ms=97,
                        error_message=None,
                    ),
                    ConnectorRun(
                        id="00000000-0000-0000-0000-000000000304",
                        search_id=search_id,
                        source="airasia",
                        status="timeout",
                        latency_ms=20105,
                        error_message="Timed out after 20s",
                    ),
                ]
            )
            await session.commit()

        async with session_factory() as session:
            result = await get_search(search_id=search_id, session=session)

        assert result.status == SearchStatus.completed
        assert result.cheapest_flight is None
        assert result.alternatives == []
        assert len(result.connector_runs) == 2
        assert all(item.offer_count == 0 for item in result.connector_runs)
        assert [item.source for item in result.failures] == ["airasia"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_get_search_connector_run_order_follows_requested_sources(tmp_path) -> None:
    engine, session_factory = await _create_session_factory(tmp_path)
    search_id = "00000000-0000-0000-0000-000000000105"
    try:
        async with session_factory() as session:
            session.add(
                SearchRequest(
                    id=search_id,
                    query_hash="hash-order",
                    query_json=_query_payload_with_sources(["trip_com", "airasia"]),
                    status=SearchStatus.completed.value,
                    created_at=datetime(2026, 2, 12, 10, 0, tzinfo=UTC),
                    started_at=datetime(2026, 2, 12, 10, 0, 1, tzinfo=UTC),
                    completed_at=datetime(2026, 2, 12, 10, 0, 4, tzinfo=UTC),
                )
            )
            # Insert in opposite order to ensure API ordering is query-driven.
            session.add_all(
                [
                    ConnectorRun(
                        id="00000000-0000-0000-0000-000000000305",
                        search_id=search_id,
                        source="airasia",
                        status="success",
                        latency_ms=121,
                        error_message=None,
                    ),
                    ConnectorRun(
                        id="00000000-0000-0000-0000-000000000306",
                        search_id=search_id,
                        source="trip_com",
                        status="success",
                        latency_ms=95,
                        error_message=None,
                    ),
                ]
            )
            await session.commit()

        async with session_factory() as session:
            result = await get_search(search_id=search_id, session=session)

        assert [item.source for item in result.connector_runs] == ["trip_com", "airasia"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_get_search_offer_returns_ticket_detail(tmp_path) -> None:
    engine, session_factory = await _create_session_factory(tmp_path)
    search_id = "00000000-0000-0000-0000-000000000103"
    offer_id = "00000000-0000-0000-0000-000000000203"
    try:
        async with session_factory() as session:
            session.add(
                SearchRequest(
                    id=search_id,
                    query_hash="hash-c",
                    query_json=_query_payload(),
                    status=SearchStatus.completed.value,
                    created_at=datetime(2026, 2, 12, 8, 0, tzinfo=UTC),
                    started_at=datetime(2026, 2, 12, 8, 0, 1, tzinfo=UTC),
                    completed_at=datetime(2026, 2, 12, 8, 0, 3, tzinfo=UTC),
                )
            )
            session.add(
                _build_offer(
                    offer_id=offer_id,
                    search_id=search_id,
                    source="trip_com",
                    total_price="259.90",
                    booking_url="https://trip.com/detail-a",
                    fare_brand="Value Saver",
                    base_price="210.00",
                    taxes="40.00",
                    fees="9.90",
                    raw_payload={"provider_offer_id": "abc123"},
                )
            )
            await session.commit()

        async with session_factory() as session:
            result = await get_search_offer(search_id=search_id, offer_id=offer_id, session=session)

        assert result.offer_id == offer_id
        assert result.fare_brand == "Value Saver"
        assert result.base_price == pytest.approx(210.0)
        assert result.taxes == pytest.approx(40.0)
        assert result.fees == pytest.approx(9.9)
        assert result.raw_payload == {"provider_offer_id": "abc123"}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_get_search_offer_missing_offer_returns_404(tmp_path) -> None:
    engine, session_factory = await _create_session_factory(tmp_path)
    search_id = "00000000-0000-0000-0000-000000000104"
    try:
        async with session_factory() as session:
            session.add(
                SearchRequest(
                    id=search_id,
                    query_hash="hash-d",
                    query_json=_query_payload(),
                    status=SearchStatus.completed.value,
                    created_at=datetime(2026, 2, 12, 9, 0, tzinfo=UTC),
                    started_at=datetime(2026, 2, 12, 9, 0, 1, tzinfo=UTC),
                    completed_at=datetime(2026, 2, 12, 9, 0, 3, tzinfo=UTC),
                )
            )
            await session.commit()

        async with session_factory() as session:
            with pytest.raises(HTTPException) as exc:
                await get_search_offer(
                    search_id=search_id,
                    offer_id="00000000-0000-0000-0000-000000009999",
                    session=session,
                )

        assert exc.value.status_code == 404
        assert exc.value.detail == "offer_id not found for search_id"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_get_search_converts_legacy_naive_offer_time_as_malaysia_local(tmp_path) -> None:
    engine, session_factory = await _create_session_factory(tmp_path)
    search_id = "00000000-0000-0000-0000-000000000106"
    try:
        malaysia_tz = ZoneInfo("Asia/Kuala_Lumpur")
        legacy_local = datetime(2026, 3, 20, 8, 10)
        expected_utc = legacy_local.replace(tzinfo=malaysia_tz).astimezone(UTC)

        async with session_factory() as session:
            session.add(
                SearchRequest(
                    id=search_id,
                    query_hash="hash-legacy-time",
                    query_json=_query_payload(),
                    status=SearchStatus.completed.value,
                    created_at=datetime(2026, 2, 12, 11, 0, tzinfo=UTC),
                    started_at=datetime(2026, 2, 12, 11, 0, 1, tzinfo=UTC),
                    completed_at=datetime(2026, 2, 12, 11, 0, 3, tzinfo=UTC),
                )
            )
            session.add(
                Offer(
                    id="00000000-0000-0000-0000-000000000204",
                    search_id=search_id,
                    source="trip_com",
                    dedup_key="legacy-time-key",
                    airline="AirAsia",
                    flight_numbers=["AK611"],
                    origin="KUL",
                    destination="BKK",
                    departure_at=legacy_local,
                    arrival_at=datetime(2026, 3, 20, 9, 20),
                    stops=0,
                    duration_minutes=70,
                    cabin="economy",
                    fare_brand=None,
                    baggage="7kg carry-on",
                    fare_rules="No refund",
                    base_price=Decimal("109.00"),
                    taxes=None,
                    fees=None,
                    total_price=109.0,
                    currency="MYR",
                    booking_url="https://trip.com/legacy",
                    deep_link_valid=True,
                    raw_payload={},
                )
            )
            await session.commit()

        async with session_factory() as session:
            result = await get_search(search_id=search_id, session=session)

        assert result.cheapest_flight is not None
        assert result.cheapest_flight.departure_at == expected_utc
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_get_search_alternatives_include_airline_variety(tmp_path) -> None:
    engine, session_factory = await _create_session_factory(tmp_path)
    search_id = "00000000-0000-0000-0000-000000000107"
    try:
        async with session_factory() as session:
            session.add(
                SearchRequest(
                    id=search_id,
                    query_hash="hash-airline-variety",
                    query_json=_query_payload(),
                    status=SearchStatus.completed.value,
                    created_at=datetime(2026, 2, 12, 12, 0, tzinfo=UTC),
                    started_at=datetime(2026, 2, 12, 12, 0, 1, tzinfo=UTC),
                    completed_at=datetime(2026, 2, 12, 12, 0, 4, tzinfo=UTC),
                )
            )
            session.add_all(
                [
                    _build_offer(
                        offer_id="00000000-0000-0000-0000-000000000211",
                        search_id=search_id,
                        source="trip_com",
                        total_price="300.00",
                        booking_url="https://trip.com/e-1",
                        airline="Emirates",
                    ),
                    _build_offer(
                        offer_id="00000000-0000-0000-0000-000000000212",
                        search_id=search_id,
                        source="trip_com",
                        total_price="320.00",
                        booking_url="https://trip.com/e-2",
                        airline="Emirates",
                    ),
                    _build_offer(
                        offer_id="00000000-0000-0000-0000-000000000213",
                        search_id=search_id,
                        source="trip_com",
                        total_price="330.00",
                        booking_url="https://trip.com/e-3",
                        airline="Emirates",
                    ),
                    _build_offer(
                        offer_id="00000000-0000-0000-0000-000000000214",
                        search_id=search_id,
                        source="trip_com",
                        total_price="380.00",
                        booking_url="https://trip.com/aa-1",
                        airline="American Airlines",
                    ),
                ]
            )
            await session.commit()

        async with session_factory() as session:
            result = await get_search(search_id=search_id, session=session)

        assert result.cheapest_flight is not None
        assert result.cheapest_flight.airline == "Emirates"
        airlines = {item.airline for item in result.alternatives}
        assert "American Airlines" in airlines
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_get_search_limits_alternatives_to_five(tmp_path) -> None:
    engine, session_factory = await _create_session_factory(tmp_path)
    search_id = "00000000-0000-0000-0000-000000000108"
    try:
        async with session_factory() as session:
            session.add(
                SearchRequest(
                    id=search_id,
                    query_hash="hash-alt-limit",
                    query_json=_query_payload(),
                    status=SearchStatus.completed.value,
                    created_at=datetime(2026, 2, 12, 13, 0, tzinfo=UTC),
                    started_at=datetime(2026, 2, 12, 13, 0, 1, tzinfo=UTC),
                    completed_at=datetime(2026, 2, 12, 13, 0, 5, tzinfo=UTC),
                )
            )
            offers = [
                _build_offer(
                    offer_id=f"00000000-0000-0000-0000-00000000022{index}",
                    search_id=search_id,
                    source="trip_com",
                    total_price=f"{200 + index * 10}.00",
                    booking_url=f"https://trip.com/alt-{index}",
                    airline=f"Airline {index}",
                )
                for index in range(7)
            ]
            session.add_all(offers)
            await session.commit()

        async with session_factory() as session:
            result = await get_search(search_id=search_id, session=session)

        assert result.cheapest_flight is not None
        assert len(result.alternatives) == 5
        assert [item.total_price for item in result.alternatives] == [
            210.0,
            220.0,
            230.0,
            240.0,
            250.0,
        ]
    finally:
        await engine.dispose()
