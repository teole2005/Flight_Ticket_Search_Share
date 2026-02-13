from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import date, timedelta
from datetime import time as dt_time
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

import httpx

from app.connectors.base import ConnectorExecutionError, FlightConnector, RawFlightOffer
from app.connectors.parsers import build_datetime, compute_duration_minutes, normalize_text
from app.schemas import SearchCreateRequest, TripType

_SCHEDULE_PATTERN = re.compile(
    r"Departs\s+(?P<departure>\d{2}:\d{2})\.\s+"
    r"Arrives\s+(?P<arrival>\d{2}:\d{2})\.\s+"
    r"Duration\s+(?P<hours>\d+)\s+hours?\s+(?P<minutes>\d+)\s+minutes?\.\s+"
    r"(?P<stops>Direct|\d+\s+stops?)",
    re.IGNORECASE,
)
logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _ResolvedStation:
    station_code: str
    city_code: str | None = None


class AirAsiaConnector(FlightConnector):
    name = "airasia"

    _UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    )
    _WEB_CHANNEL_HASH = "c5e9028b4295dcf4d7c239af8231823b520c3cc15b99ab04cde71d0ab18d65bc"
    _AIRPORT_SEARCH_URL = "https://flights.airasia.com/travel/stations/search/airports"
    _AUTH_URL = "https://www.airasia.com/api/auth"
    _BASE_CURRENCY_URL = "https://k.apiairasia.com/basecurrency/{station}"
    _DEEPLINK_URL = "https://k.apiairasia.com/deeplink/v1/encryptdeeplink"
    _LOWFARE_URL = "https://flights.airasia.com/fp/lfc/v1/lowfare"

    async def search(self, query: SearchCreateRequest) -> list[RawFlightOffer]:
        timeout = self.settings.request_timeout_seconds
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            origin = await self._resolve_station(client, query.origin, query.origin)
            destination = await self._resolve_station(
                client,
                query.destination,
                origin.station_code,
            )
            if not origin or not destination:
                raise ConnectorExecutionError(
                    f"Could not resolve stations for {query.origin}->{query.destination}"
                )

            jwt_token = await self._fetch_auth_token(client)
            lowfare_item = await self._fetch_lowfare_item(
                client=client,
                jwt_token=jwt_token,
                origin_station=origin.station_code,
                destination_station=destination.station_code,
                currency=query.currency,
                departure_date=query.departure_date,
            )
            if lowfare_item is None:
                return []

            deeplink_error: str | None = None
            try:
                deeplink_url = await self._fetch_deeplink_url(
                    client=client,
                    query=query,
                    origin_station=origin.station_code,
                    destination_station=destination.station_code,
                )
            except Exception as exc:
                deeplink_error = self._describe_exception(exc)
                deeplink_url = self._build_fallback_booking_url(
                    query=query,
                    origin_station=origin.station_code,
                    destination_station=destination.station_code,
                )
                logger.warning(
                    "AirAsia deeplink failed; using fallback URL. route=%s->%s error=%s",
                    origin.station_code,
                    destination.station_code,
                    deeplink_error,
                )

        schedule: list[dict] = []
        schedule_error: str | None = None
        if deeplink_url and self.settings.enable_browser_connectors:
            try:
                schedule = await self._extract_schedule_from_deeplink(deeplink_url)
            except Exception as exc:
                schedule_error = self._describe_exception(exc)
                logger.warning(
                    (
                        "AirAsia schedule extraction failed; continuing without schedule. "
                        "url=%s error=%s"
                    ),
                    deeplink_url,
                    schedule_error,
                )

        offer = self._build_offer(
            query=query,
            origin_station=origin.station_code,
            destination_station=destination.station_code,
            lowfare_item=lowfare_item,
            booking_url=deeplink_url,
            schedule=schedule,
            deeplink_error=deeplink_error,
            schedule_error=schedule_error,
        )
        return [offer]

    async def _resolve_station(
        self,
        client: httpx.AsyncClient,
        query_text: str,
        origin_station: str,
    ) -> _ResolvedStation:
        params = {
            "locale": "en-gb",
            "query": query_text.upper(),
            "origin": origin_station.upper(),
            "isCity": "true",
        }
        response = await client.get(
            self._AIRPORT_SEARCH_URL,
            params=params,
            headers={
                "User-Agent": self._UA,
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://www.airasia.com/",
            },
        )
        response.raise_for_status()
        stations = response.json()
        if not stations:
            raise ConnectorExecutionError(f"No stations returned for query={query_text}")

        lookup = query_text.upper()
        exact_airport = self._find_exact_airport(stations, lookup)
        if exact_airport:
            return exact_airport

        city_match = self._find_city_station(stations, lookup)
        if city_match:
            return city_match

        fallback = self._find_any_airport(stations)
        if fallback:
            return fallback

        raise ConnectorExecutionError(f"Unable to resolve airport for query={query_text}")

    def _find_exact_airport(
        self,
        stations: list[dict],
        lookup: str,
    ) -> _ResolvedStation | None:
        for item in stations:
            if str(item.get("StationType", "")).upper() != "A":
                continue
            if str(item.get("StationCode", "")).upper() != lookup:
                continue
            return _ResolvedStation(
                station_code=str(item["StationCode"]).upper(),
                city_code=str(item.get("CityCode", "")).upper() or None,
            )
        return None

    def _find_city_station(
        self,
        stations: list[dict],
        lookup: str,
    ) -> _ResolvedStation | None:
        for item in stations:
            is_city = str(item.get("StationType", "")).upper() == "C"
            if not is_city:
                continue
            station_code = str(item.get("StationCode", "")).upper()
            city_code = str(item.get("CityCode", station_code)).upper() or None
            if station_code != lookup and city_code != lookup:
                continue
            nested = item.get("Stations") or []
            selected = self._select_preferred_nested_station(nested)
            if selected:
                return _ResolvedStation(station_code=selected, city_code=city_code)
        return None

    def _find_any_airport(self, stations: list[dict]) -> _ResolvedStation | None:
        for item in stations:
            if str(item.get("StationType", "")).upper() == "A":
                return _ResolvedStation(
                    station_code=str(item["StationCode"]).upper(),
                    city_code=str(item.get("CityCode", "")).upper() or None,
                )
            nested = item.get("Stations") or []
            selected = self._select_preferred_nested_station(nested)
            if selected:
                city_code = str(item.get("CityCode", item.get("StationCode", ""))).upper() or None
                return _ResolvedStation(station_code=selected, city_code=city_code)
        return None

    def _select_preferred_nested_station(self, nested: list[dict]) -> str | None:
        for station in nested:
            if str(station.get("AAFlight", "")).lower() == "true":
                return str(station.get("StationCode", "")).upper() or None
        if nested:
            return str(nested[0].get("StationCode", "")).upper() or None
        return None

    async def _fetch_auth_token(self, client: httpx.AsyncClient) -> str:
        ga_id = f"GA1.2.{int(time.time())}.{int(time.time())}"
        response = await client.post(
            self._AUTH_URL,
            headers={
                "User-Agent": self._UA,
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/json",
                "Origin": "https://www.airasia.com",
                "Referer": "https://www.airasia.com/en/gb",
                "Channel-Hash": self._WEB_CHANNEL_HASH,
                "Ga-Id": ga_id,
            },
            json={"xaaDomain": "com"},
        )
        response.raise_for_status()
        jwt_token = response.json().get("jwt")
        if not jwt_token:
            raise ConnectorExecutionError("AirAsia auth endpoint did not return JWT token")
        return jwt_token

    async def _fetch_lowfare_item(
        self,
        *,
        client: httpx.AsyncClient,
        jwt_token: str,
        origin_station: str,
        destination_station: str,
        currency: str,
        departure_date: date,
    ) -> dict | None:
        begin_date = date.today()
        end_date = max(begin_date + timedelta(days=45), departure_date + timedelta(days=7))
        params = {
            "departStation": origin_station.upper(),
            "arrivalStation": destination_station.upper(),
            "beginDate": begin_date.strftime("%d/%m/%Y"),
            "endDate": end_date.strftime("%d/%m/%Y"),
            "currency": currency.upper(),
            "isDestinationCity": "false",
            "isOriginCity": "false",
        }

        response = await client.get(
            self._LOWFARE_URL,
            params=params,
            headers={
                "User-Agent": self._UA,
                "Accept": "application/json, text/plain, */*",
                "Authorization": f"Bearer {jwt_token}",
                "channel_hash": self._WEB_CHANNEL_HASH,
                "user-type": "anonymous",
                "Referer": "https://www.airasia.com/",
            },
        )
        if response.status_code >= 400:
            return None

        payload = response.json()
        entries = payload.get("data") or []
        target_day = departure_date.strftime("%d/%m/%Y")
        same_day = [
            item
            for item in entries
            if item.get("departureDate") == target_day and not item.get("soldOut", False)
        ]
        if not same_day:
            return None

        aa_flights = [item for item in same_day if item.get("aaFlight")]
        candidates = aa_flights if aa_flights else same_day
        priced_candidates: list[tuple[Decimal, dict]] = []
        for item in candidates:
            price = self._price_as_decimal(item)
            if price is None:
                continue
            priced_candidates.append((price, item))
        if not priced_candidates:
            return None
        return min(priced_candidates, key=lambda item: item[0])[1]

    async def _fetch_deeplink_url(
        self,
        *,
        client: httpx.AsyncClient,
        query: SearchCreateRequest,
        origin_station: str,
        destination_station: str,
    ) -> str:
        base_currency = await self._fetch_base_currency(client, origin_station, query.currency)
        trip_type_value = "roundtrip" if query.trip_type == TripType.round_trip else "oneway"
        return_date = query.return_date.isoformat() if query.return_date else "N"

        payload = {
            "params": {
                "base_currency": base_currency,
                "isBigLoyaltyRedemptionEnabled": False,
            },
            "triptype": trip_type_value,
            "culture_code": "en/gb",
            "depart": origin_station.upper(),
            "arrival": destination_station.upper(),
            "departdate": query.departure_date.isoformat(),
            "returndate": return_date,
            "totaladult": query.adults,
            "totalchild": query.children,
            "totalinfant": query.infants,
            "currency": query.currency.upper(),
            "sort": "ST",
            "promo": "N",
            "exclude_domain": False,
        }
        response = await client.post(
            self._DEEPLINK_URL,
            headers={
                "User-Agent": self._UA,
                "Accept": "text/html",
                "Content-Type": "application/json",
                "channel_hash": self._WEB_CHANNEL_HASH,
                "page": "select",
                "exclude_domain": "false",
            },
            json=payload,
        )
        response.raise_for_status()
        deeplink = response.json().get("url")
        if not deeplink:
            raise ConnectorExecutionError("AirAsia deeplink endpoint returned no URL")
        return deeplink

    async def _fetch_base_currency(
        self,
        client: httpx.AsyncClient,
        origin_station: str,
        fallback_currency: str,
    ) -> str:
        url = self._BASE_CURRENCY_URL.format(station=origin_station.upper())
        response = await client.get(
            url,
            headers={
                "User-Agent": self._UA,
                "Accept": "application/json, text/plain, */*",
            },
        )
        if response.status_code >= 400:
            return fallback_currency.upper()
        value = response.text.strip().upper()
        if len(value) == 3:
            return value
        return fallback_currency.upper()

    async def _extract_schedule_from_deeplink(self, deeplink_url: str) -> list[dict]:
        try:
            from playwright.async_api import async_playwright
        except Exception:
            return []

        body_text = ""
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=self.settings.browser_headless)
            context = await browser.new_context()
            page = await context.new_page()
            await page.goto(
                deeplink_url,
                wait_until="domcontentloaded",
                timeout=self.settings.request_timeout_seconds * 1000,
            )
            wait_ms = max(self.settings.airasia_wait_ms, 12000)
            await page.wait_for_timeout(wait_ms)
            body_text = normalize_text(await page.locator("body").inner_text())
            await browser.close()

        if not body_text:
            return []

        seen: set[tuple[str, str, int]] = set()
        schedules: list[dict] = []
        for match in _SCHEDULE_PATTERN.finditer(body_text):
            dep = match.group("departure")
            arr = match.group("arrival")
            duration = (int(match.group("hours")) * 60) + int(match.group("minutes"))
            stops_text = match.group("stops")
            stops = 0 if "direct" in stops_text.lower() else self._extract_stop_count(stops_text)
            key = (dep, arr, duration)
            if key in seen:
                continue
            seen.add(key)
            schedules.append(
                {
                    "departure": dep,
                    "arrival": arr,
                    "duration_minutes": duration,
                    "stops": stops,
                }
            )
        return schedules

    def _build_offer(
        self,
        *,
        query: SearchCreateRequest,
        origin_station: str,
        destination_station: str,
        lowfare_item: dict,
        booking_url: str,
        schedule: list[dict],
        deeplink_error: str | None,
        schedule_error: str | None,
    ) -> RawFlightOffer:
        selected = schedule[0] if schedule else None
        dep_time = selected["departure"] if selected else None
        arr_time = selected["arrival"] if selected else None
        departure_at = build_datetime(
            query.departure_date,
            self._parse_time(dep_time),
            fallback_hour=9,
        )
        arrival_at = build_datetime(
            query.departure_date,
            self._parse_time(arr_time),
            fallback_hour=12,
        )
        if arrival_at <= departure_at:
            arrival_at += timedelta(days=1)

        duration_minutes = (
            selected["duration_minutes"]
            if selected
            else compute_duration_minutes(departure_at, arrival_at)
        )
        stops = selected["stops"] if selected else 0

        total_price = self._price_as_decimal(lowfare_item)
        if total_price is None:
            raise ConnectorExecutionError("AirAsia lowfare response did not include a valid price")

        fare_rule_notes = ["Calendar fare. Final fare is confirmed on booking page."]
        if deeplink_error:
            fare_rule_notes.append("Booking deeplink unavailable, fallback search link used.")
        if schedule_error:
            fare_rule_notes.append("Detailed schedule extraction unavailable.")
        fare_rules = " ".join(fare_rule_notes)

        return RawFlightOffer(
            source=self.name,
            airline="AirAsia",
            flight_numbers=[],
            origin=origin_station,
            destination=destination_station,
            departure_at=departure_at,
            arrival_at=arrival_at,
            stops=stops,
            duration_minutes=duration_minutes,
            cabin=query.cabin.value,
            fare_brand=str(lowfare_item.get("airlineProfile", "")) or None,
            baggage=None,
            fare_rules=fare_rules,
            base_price=total_price,
            taxes=None,
            fees=None,
            total_price=total_price,
            currency=query.currency.upper(),
            booking_url=booking_url,
            raw_payload={
                "lowfare_item": lowfare_item,
                "schedule_count": len(schedule),
                "schedule_sample": schedule[:3],
                "deeplink_error": deeplink_error,
                "schedule_error": schedule_error,
            },
        )

    def _price_as_decimal(self, item: dict) -> Decimal | None:
        try:
            return Decimal(str(item.get("price")))
        except (InvalidOperation, TypeError):
            return None

    def _extract_stop_count(self, text: str) -> int:
        match = re.search(r"(\d+)", text)
        return int(match.group(1)) if match else 0

    def _parse_time(self, value: str | None):
        if not value or ":" not in value:
            return None
        hour, minute = value.split(":", maxsplit=1)
        try:
            return dt_time(hour=int(hour), minute=int(minute))
        except ValueError:
            return None

    def _build_fallback_booking_url(
        self,
        *,
        query: SearchCreateRequest,
        origin_station: str,
        destination_station: str,
    ) -> str:
        params = {
            "origin": origin_station.upper(),
            "destination": destination_station.upper(),
            "departureDate": query.departure_date.isoformat(),
            "returnDate": query.return_date.isoformat() if query.return_date else "",
            "tripType": "round_trip" if query.trip_type == TripType.round_trip else "one_way",
            "adults": query.adults,
            "children": query.children,
            "infants": query.infants,
            "currency": query.currency.upper(),
        }
        return f"https://www.airasia.com/en/gb?{urlencode(params)}"

    def _describe_exception(self, exc: Exception) -> str:
        message = str(exc).strip()
        if message:
            return f"{type(exc).__name__}: {message}"
        return f"{type(exc).__name__}: no details provided"
