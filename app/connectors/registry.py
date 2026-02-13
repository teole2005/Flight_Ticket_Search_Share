from __future__ import annotations

from app.connectors.airasia import AirAsiaConnector
from app.connectors.base import FlightConnector
from app.connectors.trip_com import TripComConnector


class ConnectorRegistry:
    def __init__(self, settings: object) -> None:
        self.settings = settings
        self._connector_map = {
            "trip_com": TripComConnector,
            "airasia": AirAsiaConnector,
        }

    def build(self, requested_sources: list[str]) -> list[FlightConnector]:
        connectors: list[FlightConnector] = []
        for source in requested_sources:
            cls = self._connector_map.get(source.lower())
            if not cls:
                continue
            connectors.append(cls(self.settings))
        return connectors

    @property
    def available_sources(self) -> list[str]:
        return sorted(self._connector_map.keys())

