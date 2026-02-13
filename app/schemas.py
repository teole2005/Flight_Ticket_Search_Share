from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class TripType(StrEnum):
    one_way = "one_way"
    round_trip = "round_trip"


class CabinClass(StrEnum):
    economy = "economy"
    premium_economy = "premium_economy"
    business = "business"
    first = "first"


class StopPreference(StrEnum):
    any = "any"
    non_stop = "non_stop"
    with_stops = "with_stops"
    # Backward compatibility for stored/older client payloads.
    multiple_stops = "multiple_stops"


class SearchStatus(StrEnum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"


class SearchCreateRequest(BaseModel):
    origin: str = Field(..., min_length=3, max_length=3)
    destination: str = Field(..., min_length=3, max_length=3)
    departure_date: date
    return_date: date | None = None
    trip_type: TripType = TripType.round_trip
    adults: int = Field(default=1, ge=1, le=9)
    children: int = Field(default=0, ge=0, le=9)
    infants: int = Field(default=0, ge=0, le=9)
    cabin: CabinClass = CabinClass.economy
    currency: str = Field(default="MYR", min_length=3, max_length=3)
    stop_preference: StopPreference = StopPreference.any
    sources: list[str] | None = None

    @field_validator("origin", "destination")
    @classmethod
    def _validate_iata(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("currency")
    @classmethod
    def _validate_currency(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("sources", mode="before")
    @classmethod
    def _normalize_sources(cls, value: list[str] | str | None) -> list[str] | None:
        if value is None:
            return None
        if isinstance(value, str):
            return [item.strip().lower() for item in value.split(",") if item.strip()]
        return [item.strip().lower() for item in value if item.strip()]

    @model_validator(mode="after")
    def _validate_dates(self) -> SearchCreateRequest:
        if self.trip_type == TripType.round_trip and self.return_date is None:
            raise ValueError("return_date is required when trip_type is round_trip")
        if self.return_date and self.return_date < self.departure_date:
            raise ValueError("return_date must be on or after departure_date")
        return self


class SearchCreateResponse(BaseModel):
    search_id: str
    status: SearchStatus
    created_at: datetime


class FlightOfferOut(BaseModel):
    offer_id: str
    source: str
    airline: str
    flight_numbers: list[str]
    departure_at: datetime
    arrival_at: datetime
    stops: int
    duration_minutes: int
    cabin: str | None
    baggage: str | None
    fare_rules: str | None
    total_price: float
    currency: str
    booking_url: str
    deep_link_valid: bool


class FlightOfferDetailOut(FlightOfferOut):
    fare_brand: str | None = None
    base_price: float | None = None
    taxes: float | None = None
    fees: float | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)


class ConnectorFailureOut(BaseModel):
    source: str
    status: str
    message: str


class ConnectorRunOut(BaseModel):
    source: str
    status: str
    latency_ms: int
    error_message: str | None = None
    offer_count: int = 0


class SearchResultResponse(BaseModel):
    search_id: str
    status: SearchStatus
    query: SearchCreateRequest
    cheapest_flight: FlightOfferOut | None = None
    alternatives: list[FlightOfferOut] = Field(default_factory=list)
    price_last_checked_at: datetime | None = None
    failures: list[ConnectorFailureOut] = Field(default_factory=list)
    connector_runs: list[ConnectorRunOut] = Field(default_factory=list)


class ConnectorHealthItem(BaseModel):
    source: str
    status: str
    last_latency_ms: int | None = None
    last_error: str | None = None
    last_checked_at: datetime | None = None


class ConnectorHealthResponse(BaseModel):
    connectors: list[ConnectorHealthItem]
