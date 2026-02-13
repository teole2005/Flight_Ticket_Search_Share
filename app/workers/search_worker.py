from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.connectors.base import RawFlightOffer
from app.connectors.registry import ConnectorRegistry
from app.models import ConnectorRun, Offer, SearchRequest
from app.schemas import SearchCreateRequest, SearchStatus
from app.services.cache import CacheClient
from app.services.dedup import build_dedup_key, deduplicate_offers
from app.services.fx import FxService
from app.services.link_validator import LinkValidator
from app.services.normalizer import normalize_offers
from app.services.orchestrator import ConnectorRunResult, execute_connectors
from app.services.query_hash import cache_key_for_query_hash
from app.services.ranking import rank_offers
from app.services.stops import filter_offers_by_stops

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class SearchWorker:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        cache_client: CacheClient,
        settings: Settings,
    ) -> None:
        self._session_factory = session_factory
        self._cache = cache_client
        self._settings = settings
        self._registry = ConnectorRegistry(settings)
        self._fx_service = FxService(
            ttl_seconds=settings.fx_rate_ttl_seconds,
            timeout_seconds=settings.request_timeout_seconds,
        )
        self._link_validator = LinkValidator(timeout_seconds=settings.request_timeout_seconds)
        self._tasks: set[asyncio.Task[None]] = set()

    def launch(self, search_id: str) -> None:
        task = asyncio.create_task(self._run(search_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def shutdown(self) -> None:
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        await self._fx_service.close()
        await self._link_validator.close()

    async def _run(self, search_id: str) -> None:
        logger.info("Running search job search_id=%s", search_id)
        try:
            async with self._session_factory() as session:
                search = await session.get(SearchRequest, search_id)
                if search is None:
                    logger.warning("Search request not found for job search_id=%s", search_id)
                    return

                search.status = SearchStatus.running.value
                search.started_at = _utcnow()
                await session.commit()
                query = SearchCreateRequest.model_validate(search.query_json)
                query_hash = search.query_hash

            cached_payload = await self._cache.get_json(cache_key_for_query_hash(query_hash))
            if cached_payload:
                await self._hydrate_from_cache(search_id, cached_payload)
                logger.info("Search loaded from cache search_id=%s", search_id)
                return

            source_list = query.sources if query.sources else self._settings.default_sources
            connectors = self._registry.build(source_list)
            if not connectors:
                raise RuntimeError("No valid connectors requested")

            runs = await execute_connectors(
                query=query,
                connectors=connectors,
                timeout_seconds=self._settings.connector_timeout_seconds,
                retries=self._settings.connector_retries,
                max_parallel=self._settings.max_parallel_connectors,
            )

            raw_offers: list[RawFlightOffer] = []
            for run in runs:
                raw_offers.extend(run.offers)

            normalized = await normalize_offers(raw_offers, query.currency, self._fx_service)
            filtered = filter_offers_by_stops(normalized, query.stop_preference)
            deduped = deduplicate_offers(filtered)
            ranked = rank_offers(deduped)[: self._settings.max_offers_per_search]
            await self._validate_links(ranked)

            await self._persist_result(
                search_id=search_id,
                offers=ranked,
                connector_runs=runs,
            )
            if ranked:
                await self._cache.set_json(
                    cache_key_for_query_hash(query_hash),
                    {
                        "offers": [offer.to_jsonable() for offer in ranked],
                        "connector_runs": [
                            {
                                "source": run.source,
                                "status": run.status,
                                "latency_ms": run.latency_ms,
                                "error_message": run.error_message,
                            }
                            for run in runs
                        ],
                    },
                    ttl_seconds=self._settings.cache_ttl_seconds,
                )
            else:
                logger.info("Skipping cache for empty result search_id=%s", search_id)
            logger.info(
                "Search completed search_id=%s offers=%s sources=%s",
                search_id,
                len(ranked),
                ",".join(source_list),
            )
        except Exception as exc:
            logger.exception("Search failed search_id=%s", search_id)
            await self._mark_failed(search_id, str(exc))

    async def _validate_links(self, offers: list[RawFlightOffer]) -> None:
        if not offers:
            return
        tasks = [self._link_validator.validate(offer.booking_url) for offer in offers]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for offer, result in zip(offers, results, strict=True):
            offer.deep_link_valid = bool(result) if not isinstance(result, Exception) else False

    async def _persist_result(
        self,
        *,
        search_id: str,
        offers: list[RawFlightOffer],
        connector_runs: list[ConnectorRunResult],
    ) -> None:
        async with self._session_factory() as session:
            search = await session.get(SearchRequest, search_id)
            if search is None:
                return

            await session.execute(delete(Offer).where(Offer.search_id == search_id))
            await session.execute(delete(ConnectorRun).where(ConnectorRun.search_id == search_id))

            offer_models: list[Offer] = []
            for offer in offers:
                offer_models.append(
                    Offer(
                        id=str(uuid4()),
                        search_id=search_id,
                        source=offer.source,
                        dedup_key=build_dedup_key(offer),
                        airline=offer.airline,
                        flight_numbers=offer.flight_numbers,
                        origin=offer.origin,
                        destination=offer.destination,
                        departure_at=_as_utc(offer.departure_at),
                        arrival_at=_as_utc(offer.arrival_at),
                        stops=offer.stops,
                        duration_minutes=offer.duration_minutes,
                        cabin=offer.cabin,
                        fare_brand=offer.fare_brand,
                        baggage=offer.baggage,
                        fare_rules=offer.fare_rules,
                        base_price=_decimal_to_float(offer.base_price),
                        taxes=_decimal_to_float(offer.taxes),
                        fees=_decimal_to_float(offer.fees),
                        total_price=float(offer.total_price),
                        currency=offer.currency,
                        booking_url=offer.booking_url,
                        deep_link_valid=offer.deep_link_valid,
                        raw_payload={
                            "_stored_timezone": "utc",
                            **offer.raw_payload,
                        },
                    )
                )
            if offer_models:
                session.add_all(offer_models)

            run_models = [
                ConnectorRun(
                    id=str(uuid4()),
                    search_id=search_id,
                    source=run.source,
                    status=run.status,
                    latency_ms=run.latency_ms,
                    error_message=run.error_message,
                )
                for run in connector_runs
            ]
            if run_models:
                session.add_all(run_models)

            search.status = SearchStatus.completed.value
            search.error_message = None
            search.completed_at = _utcnow()
            await session.commit()

    async def _hydrate_from_cache(self, search_id: str, payload: dict) -> None:
        offers = [RawFlightOffer.from_jsonable(item) for item in payload.get("offers", [])]
        runs = [
            ConnectorRunResult(
                source=item.get("source", "unknown"),
                status=item.get("status", "success"),
                latency_ms=int(item.get("latency_ms", 0)),
                offers=[],
                error_message=item.get("error_message"),
            )
            for item in payload.get("connector_runs", [])
        ]
        await self._persist_result(search_id=search_id, offers=offers, connector_runs=runs)

    async def _mark_failed(self, search_id: str, error_message: str) -> None:
        async with self._session_factory() as session:
            search = await session.get(SearchRequest, search_id)
            if search is None:
                return
            search.status = SearchStatus.failed.value
            search.error_message = error_message
            search.completed_at = _utcnow()
            await session.commit()


def _decimal_to_float(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(value)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
