import asyncio
import logging
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from playwright.async_api import async_playwright

from app.connectors.base import ConnectorExecutionError, FlightConnector, RawFlightOffer
from app.schemas import SearchCreateRequest

logger = logging.getLogger(__name__)

def run_in_proactor_loop(coro):
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    new_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(new_loop)
    try:
        return new_loop.run_until_complete(coro)
    finally:
        new_loop.close()

class AirpazConnector(FlightConnector):
    name = "airpaz"

    def __init__(self, settings: Any) -> None:
        super().__init__(settings)

    async def search(self, query: SearchCreateRequest) -> list[RawFlightOffer]:
        if not getattr(self.settings, "enable_browser_connectors", True):
            raise ConnectorExecutionError("Airpaz connector requires browser connectors to be enabled")

        logger.info(f"AirpazConnector: Starting search for {query.origin} -> {query.destination}")
        
        return await asyncio.to_thread(
            run_in_proactor_loop,
            self._run_scraper(query)
        )

    async def _scrape_flights(self, page, url: str, query: SearchCreateRequest) -> list[RawFlightOffer]:
        logger.info("\n[ACTION]: Scraping flight data from page...")
        offers = []

        try:
            await page.wait_for_selector('div[data-testid^="flightResultCard-detail_"]', timeout=20000)
            await asyncio.sleep(2)
        except Exception:
            logger.warning("Timeout waiting for Airpaz flight cards...")
            return offers

        flight_cards = await page.locator('div[data-testid^="flightResultCard-detail_"]').all()
        logger.info(f"\nFound {len(flight_cards)} flights on Airpaz.")

        for card in flight_cards:
            try:
                # 1. Airline Name
                airline_loc = card.locator("span.line-clamp-1").first
                if await airline_loc.count() == 0:
                    continue
                airline = await airline_loc.inner_text()

                # 2. Departure & Arrival Times
                times_loc = card.locator("p.font-bold.text-medium.text-gray-darkest")
                times = await times_loc.all_inner_texts()
                if len(times) < 2:
                    continue
                dep_time = times[0]
                arr_time = times[1]

                # 3. Price
                price_loc = card.locator("span.font-bold.text-medium.text-gray-darkest").filter(has_text="RM")
                if await price_loc.count() == 0:
                    price_loc = card.locator("span.font-bold.text-medium.text-gray-darkest").last
                    
                price_str = await price_loc.inner_text()
                
                # 4. Duration
                duration_loc = card.locator("div.absolute.-top-20")
                duration_str = await duration_loc.inner_text() if await duration_loc.count() > 0 else ""

                def clean_price(p_str):
                    import re
                    match = re.search(r'[\d,]+(?:\.\d+)?', p_str)
                    if match:
                        return Decimal(match.group(0).replace(",", ""))
                    return None

                eco_price = clean_price(price_str)
                if not eco_price:
                    continue

                def parse_duration(d_str):
                    import re
                    h = 0
                    m = 0
                    h_match = re.search(r'(\d+)\s*h', d_str, re.IGNORECASE)
                    if h_match:
                        h = int(h_match.group(1))
                    m_match = re.search(r'(\d+)\s*m', d_str, re.IGNORECASE)
                    if m_match:
                        m = int(m_match.group(1))
                    return h * 60 + m

                duration_minutes = parse_duration(duration_str)

                dep_dt = datetime.combine(query.departure_date, datetime.strptime(dep_time, "%H:%M").time())
                arr_dt = datetime.combine(query.departure_date, datetime.strptime(arr_time, "%H:%M").time())
                
                if arr_dt < dep_dt:
                    arr_dt += timedelta(days=1)

                full_text = await card.inner_text()
                import re
                stops_match = re.search(r'(\d+)\s*stop', full_text, re.IGNORECASE)
                if stops_match:
                    stops_count = int(stops_match.group(1))
                    stops_display = f"{stops_count} Stop(s)"
                else:
                    stops_count = 0
                    stops_display = "Direct"

                offer = RawFlightOffer(
                    source=self.name,
                    airline=airline.strip(),
                    flight_numbers=[],
                    origin=query.origin,
                    destination=query.destination,
                    departure_at=dep_dt,
                    arrival_at=arr_dt,
                    stops=stops_count,
                    duration_minutes=duration_minutes,
                    cabin=query.cabin.capitalize(),
                    fare_brand=None,
                    baggage=None,
                    fare_rules=None,
                    base_price=eco_price,
                    taxes=Decimal("0"),
                    fees=Decimal("0"),
                    total_price=eco_price,
                    currency=query.currency, 
                    booking_url=url,
                    raw_payload={
                        "airline": airline,
                        "departure": dep_time,
                        "arrival": arr_time,
                        "duration": duration_str,
                        "price": price_str,
                        "stops": stops_display
                    },
                )
                offers.append(offer)

            except Exception as e:
                logger.debug(f"Error parsing Airpaz row: {e}")
                continue
                
        logger.info(f"AirpazConnector: Successfully extracted {len(offers)} valid flight offer(s)")
        return offers

    async def _run_scraper(self, query: SearchCreateRequest) -> list[RawFlightOffer]:
        async with async_playwright() as p:
            # headless = getattr(self.settings, "browser_headless", False)
            browser = await p.chromium.launch(headless=False, slow_mo=500)
            page = await browser.new_page()
            
            cabin = query.cabin.lower()
            if cabin == "premium_economy":
                cabin = "premium"
            
            dep_date = query.departure_date.strftime("%Y-%m-%d")
            
            url = f"https://www.airpaz.com/en/flight/search?adult={query.adults}&arrAirport={query.destination}&cabin={cabin}&child={query.children}&depAirport={query.origin}&depDate={dep_date}&infant={query.infants}"
            
            if query.return_date:
                ret_date = query.return_date.strftime("%Y-%m-%d")
                url += f"&retDate={ret_date}"
            
            logger.info(f"[ACTION] Navigating to {url}")
            await page.goto(url, wait_until="domcontentloaded")

            offers = await self._scrape_flights(page, url, query)
            
            await browser.close()
            return offers
