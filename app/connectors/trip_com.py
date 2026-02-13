from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlencode, urljoin

from app.connectors.base import ConnectorExecutionError, FlightConnector, RawFlightOffer
from app.connectors.browser_tools import BrowserCard, scrape_cards
from app.connectors.parsers import (
    build_datetime,
    compute_duration_minutes,
    extract_airline_name,
    extract_flight_numbers,
    extract_price,
    extract_stops,
    extract_times,
    normalize_text,
)
from app.schemas import SearchCreateRequest, TripType


class TripComConnector(FlightConnector):
    name = "trip_com"

    async def search(self, query: SearchCreateRequest) -> list[RawFlightOffer]:
        if not self.settings.enable_browser_connectors:
            raise ConnectorExecutionError(
                "trip_com connector requires browser connectors to be enabled"
            )

        search_url = self._build_search_url(query)
        cards = await scrape_cards(
            url=search_url,
            card_selector=self.settings.trip_com_result_selector,
            link_selector=self.settings.trip_com_link_selector,
            wait_ms=self.settings.trip_com_wait_ms,
            max_cards=self.settings.trip_com_max_cards,
            headless=self.settings.browser_headless,
            timeout_ms=self.settings.request_timeout_seconds * 1000,
        )

        offers: list[RawFlightOffer] = []
        for card in cards:
            parsed_offer = self._parse_card(card, query, search_url)
            if parsed_offer:
                offers.append(parsed_offer)
        return offers

    def _build_search_url(self, query: SearchCreateRequest) -> str:
        params = {
            "dcity": query.origin,
            "acity": query.destination,
            "ddate": query.departure_date.isoformat(),
            "triptype": "rt" if query.trip_type == TripType.round_trip else "ow",
            "class": query.cabin.value,
            "quantity": query.adults + query.children,
        }
        if query.return_date:
            params["rdate"] = query.return_date.isoformat()
        return f"https://www.trip.com/flights/showfarefirst?{urlencode(params)}"

    def _parse_card(
        self, card: BrowserCard, query: SearchCreateRequest, search_url: str
    ) -> RawFlightOffer | None:
        text = normalize_text(card.text)
        if not text:
            return None

        total_price, currency = extract_price(text, query.currency)
        if total_price is None:
            return None

        departure_time, arrival_time = extract_times(text)
        departure_at = build_datetime(query.departure_date, departure_time, fallback_hour=9)
        arrival_at = build_datetime(query.departure_date, arrival_time, fallback_hour=12)
        if arrival_at <= departure_at:
            arrival_at += timedelta(days=1)
        duration_minutes = compute_duration_minutes(departure_at, arrival_at)

        booking_url = search_url
        if card.link:
            booking_url = urljoin(search_url, card.link)

        flight_numbers = extract_flight_numbers(text)
        airline = extract_airline_name(text, default="Unknown airline")

        return RawFlightOffer(
            source=self.name,
            airline=airline,
            flight_numbers=flight_numbers,
            origin=query.origin,
            destination=query.destination,
            departure_at=departure_at,
            arrival_at=arrival_at,
            stops=extract_stops(text),
            duration_minutes=duration_minutes,
            cabin=query.cabin.value,
            fare_brand=None,
            baggage=None,
            fare_rules=None,
            base_price=Decimal(total_price),
            taxes=None,
            fees=None,
            total_price=Decimal(total_price),
            currency=currency,
            booking_url=booking_url,
            raw_payload={"text": text},
        )
