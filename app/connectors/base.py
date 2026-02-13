from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.schemas import SearchCreateRequest


class ConnectorExecutionError(RuntimeError):
    """Raised when a connector fails to fetch or parse results."""


@dataclass(slots=True)
class RawFlightOffer:
    source: str
    airline: str
    flight_numbers: list[str]
    origin: str
    destination: str
    departure_at: datetime
    arrival_at: datetime
    stops: int
    duration_minutes: int
    cabin: str | None
    fare_brand: str | None
    baggage: str | None
    fare_rules: str | None
    base_price: Decimal | None
    taxes: Decimal | None
    fees: Decimal | None
    total_price: Decimal
    currency: str
    booking_url: str
    raw_payload: dict[str, Any]
    deep_link_valid: bool = False

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "airline": self.airline,
            "flight_numbers": self.flight_numbers,
            "origin": self.origin,
            "destination": self.destination,
            "departure_at": self.departure_at.isoformat(),
            "arrival_at": self.arrival_at.isoformat(),
            "stops": self.stops,
            "duration_minutes": self.duration_minutes,
            "cabin": self.cabin,
            "fare_brand": self.fare_brand,
            "baggage": self.baggage,
            "fare_rules": self.fare_rules,
            "base_price": str(self.base_price) if self.base_price is not None else None,
            "taxes": str(self.taxes) if self.taxes is not None else None,
            "fees": str(self.fees) if self.fees is not None else None,
            "total_price": str(self.total_price),
            "currency": self.currency,
            "booking_url": self.booking_url,
            "raw_payload": self.raw_payload,
            "deep_link_valid": self.deep_link_valid,
        }

    @classmethod
    def from_jsonable(cls, payload: dict[str, Any]) -> RawFlightOffer:
        return cls(
            source=payload["source"],
            airline=payload["airline"],
            flight_numbers=payload.get("flight_numbers", []),
            origin=payload["origin"],
            destination=payload["destination"],
            departure_at=datetime.fromisoformat(payload["departure_at"]),
            arrival_at=datetime.fromisoformat(payload["arrival_at"]),
            stops=int(payload.get("stops", 0)),
            duration_minutes=int(payload.get("duration_minutes", 0)),
            cabin=payload.get("cabin"),
            fare_brand=payload.get("fare_brand"),
            baggage=payload.get("baggage"),
            fare_rules=payload.get("fare_rules"),
            base_price=Decimal(payload["base_price"]) if payload.get("base_price") else None,
            taxes=Decimal(payload["taxes"]) if payload.get("taxes") else None,
            fees=Decimal(payload["fees"]) if payload.get("fees") else None,
            total_price=Decimal(payload["total_price"]),
            currency=payload["currency"],
            booking_url=payload["booking_url"],
            raw_payload=payload.get("raw_payload", {}),
            deep_link_valid=bool(payload.get("deep_link_valid", False)),
        )


class FlightConnector(ABC):
    name: str

    def __init__(self, settings: Any) -> None:
        self.settings = settings

    @abstractmethod
    async def search(self, query: SearchCreateRequest) -> list[RawFlightOffer]:
        raise NotImplementedError
