from __future__ import annotations

import asyncio
import logging
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
from app.schemas import SearchCreateRequest, StopPreference, TripType

logger = logging.getLogger(__name__)
_EMPTY_RESULT_RETRIES = 2
_RETRY_BACKOFF_SECONDS = 1.2
_CLEAR_NON_STOP_FILTER_SCRIPT = """
() => {
  const normalize = (value) => String(value || '').toLowerCase().replace(/\\s+/g, ' ').trim();
  const hasNonStopText = (value) => {
    const text = normalize(value);
    return (
      text.includes('non-stop')
      || text.includes('non stop')
      || text === 'nonstop'
      || text.includes('direct')
    );
  };

  const clickIfActive = (node) => {
    if (!(node instanceof Element)) {
      return false;
    }
    const ariaChecked = normalize(node.getAttribute('aria-checked'));
    const ariaPressed = normalize(node.getAttribute('aria-pressed'));
    if (ariaChecked === 'true' || ariaPressed === 'true') {
      node.click();
      return true;
    }
    if (node instanceof HTMLInputElement) {
      if (node.checked) {
        node.click();
        return true;
      }
      return false;
    }
    const input = node.querySelector(\"input[type='checkbox'], input[type='radio']\");
    if (input instanceof HTMLInputElement && input.checked) {
      input.click();
      return true;
    }
    return false;
  };

  const selectors = [
    \"input[type='checkbox']\",
    \"input[type='radio']\",
    \"[role='checkbox']\",
    \"[role='button']\",
    'button',
    'label',
  ];
  const candidates = Array.from(document.querySelectorAll(selectors.join(', ')));
  for (const candidate of candidates) {
    const text = normalize(candidate.textContent);
    const ariaLabel = normalize(candidate.getAttribute('aria-label'));
    if (!hasNonStopText(text) && !hasNonStopText(ariaLabel)) {
      continue;
    }
    if (clickIfActive(candidate)) {
      continue;
    }
    const parentLabel = candidate.closest('label');
    if (parentLabel && clickIfActive(parentLabel)) {
      continue;
    }
  }
}
"""


class TripComConnector(FlightConnector):
    name = "trip_com"

    async def search(self, query: SearchCreateRequest) -> list[RawFlightOffer]:
        if not self.settings.enable_browser_connectors:
            raise ConnectorExecutionError(
                "trip_com connector requires browser connectors to be enabled"
            )

        search_url = self._build_search_url(query)
        pre_collect_script = self._build_pre_collect_script(query)
        last_cards: list[BrowserCard] = []
        last_offers: list[RawFlightOffer] = []

        for attempt in range(_EMPTY_RESULT_RETRIES + 1):
            cards = await scrape_cards(
                url=search_url,
                card_selector=self.settings.trip_com_result_selector,
                link_selector=self.settings.trip_com_link_selector,
                wait_ms=self.settings.trip_com_wait_ms,
                max_cards=self.settings.trip_com_max_cards,
                headless=self.settings.browser_headless,
                timeout_ms=self.settings.request_timeout_seconds * 1000,
                pre_collect_script=pre_collect_script,
            )

            offers: list[RawFlightOffer] = []
            for card in cards:
                parsed_offer = self._parse_card(card, query, search_url)
                if parsed_offer:
                    offers.append(parsed_offer)

            logger.info(
                "trip_com scrape attempt=%s route=%s->%s cards=%s offers=%s",
                attempt + 1,
                query.origin,
                query.destination,
                len(cards),
                len(offers),
            )
            if offers:
                logger.info(
                    "trip_com scrape completed route=%s->%s cards=%s offers=%s",
                    query.origin,
                    query.destination,
                    len(cards),
                    len(offers),
                )
                return offers

            last_cards = cards
            last_offers = offers
            if attempt >= _EMPTY_RESULT_RETRIES:
                break
            if not self._should_retry_empty_scrape(cards, search_url):
                break
            await asyncio.sleep(_RETRY_BACKOFF_SECONDS * (attempt + 1))

        logger.info(
            "trip_com scrape completed route=%s->%s cards=%s offers=%s",
            query.origin,
            query.destination,
            len(last_cards),
            len(last_offers),
        )
        return last_offers

    def _build_pre_collect_script(self, query: SearchCreateRequest) -> str | None:
        if query.stop_preference == StopPreference.non_stop:
            return None
        return _CLEAR_NON_STOP_FILTER_SCRIPT

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

    def _should_retry_empty_scrape(self, cards: list[BrowserCard], search_url: str) -> bool:
        if not cards:
            return True
        if len(cards) > 1:
            return False
        card = cards[0]
        if card.link and card.link != search_url:
            return False

        text = card.text.lower()
        blocked_markers = (
            "captcha",
            "verify you are human",
            "access denied",
            "just a moment",
            "checking your browser",
            "unusual traffic",
            "security check",
        )
        if any(marker in text for marker in blocked_markers):
            return True

        no_result_markers = (
            "no flights",
            "no result",
            "no fares",
            "no available flights",
        )
        # Retry once when a selector miss likely captured generic page text.
        return not any(marker in text for marker in no_result_markers)
