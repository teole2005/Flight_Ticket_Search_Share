import asyncio
import logging
import sys
from datetime import datetime
from decimal import Decimal
from typing import Any

from playwright.async_api import async_playwright

from app.connectors.base import ConnectorExecutionError, FlightConnector, RawFlightOffer
from app.schemas import SearchCreateRequest, TripType

logger = logging.getLogger(__name__)

# Note: Playwright requires ProactorEventLoop on Windows because it uses subprocesses.
def run_in_proactor_loop(coro):
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    new_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(new_loop)
    try:
        return new_loop.run_until_complete(coro)
    finally:
        new_loop.close()


class MynztripConnector(FlightConnector):
    name = "mynztrip"

    def __init__(self, settings: Any) -> None:
        super().__init__(settings)
        self.market_data = []

    async def search(self, query: SearchCreateRequest) -> list[RawFlightOffer]:
        if not getattr(self.settings, "enable_browser_connectors", True):
            raise ConnectorExecutionError("mynztrip connector requires browser connectors to be enabled")

        logger.info("MynztripConnector: Starting search for %s -> %s", query.origin, query.destination)
        
        target_country = "Malaysia"
        origin = query.origin
        dest = query.destination
        date_str = query.departure_date.strftime("%Y-%m-%d")
        return_date_str = query.return_date.strftime("%Y-%m-%d") if query.return_date else None
        
        multi_city = None
            
        return await asyncio.to_thread(
            run_in_proactor_loop,
            self._run_scraper(target_country, origin, dest, date_str, return_date_str, multi_city)
        )

    async def _run_scraper(self, target_country, origin, dest, date, return_date, multi_city):
        async with async_playwright() as p:
            logger.info("MynztripConnector: Successfully launched browser (Headless)")
            browser = await p.chromium.launch(
                headless=getattr(self.settings, "browser_headless", True),
                args=["--disable-blink-features=AutomationControlled"]
            )
            
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 14_8 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.2 Mobile/15E148 Safari/604.1",
                viewport={"width": 375, "height": 667}
            )
            page = await context.new_page()
            page.set_default_timeout(60000)
            page.set_default_navigation_timeout(60000)

            async def handle_response(response):
                if "market-list-b2c" in response.url:
                    try:
                        res_json = await response.json()
                        self.market_data = res_json.get("data", res_json)
                        logger.info("MynztripConnector: Captured Market List JSON")
                    except Exception:
                        pass
            
            page.on("response", handle_response)

            logger.info("MynztripConnector: Connecting to https://m.mynztrip.com/")
            try:
                await page.goto("https://m.mynztrip.com/", wait_until="networkidle", timeout=60000)
            except Exception as e:
                logger.error("MynztripConnector: Failed to load page: %s", str(e), exc_info=True)
                await browser.close()
                return []
            
            await asyncio.sleep(10)

            market_id, currency = None, None
            if isinstance(self.market_data, list):
                for m in self.market_data:
                    name = m.get("market_name") or m.get("name") or ""
                    if target_country.lower() in name.lower():
                        market_id = m.get("id")
                        currency = m.get("currency_code")
                        break

            if not market_id:
                logger.error("MynztripConnector: Market info not found for %s", target_country)
                await browser.close()
                return []

            logger.info("MynztripConnector: Market matched! Target: %s | ID: %s | Currency: %s", target_country, market_id, currency)

            journey_type = 1
            routes = []

            if multi_city:
                journey_type = 3
                routes = multi_city
                logger.info("MynztripConnector: Searching Multi-City (%d routes)", len(routes))
            else:
                routes = [{
                    "origin": origin,
                    "destination": dest,
                    "departureDate": date
                }]
                if return_date:
                    journey_type = 2
                    routes.append({
                        "origin": dest,
                        "destination": origin,
                        "departureDate": return_date
                    })
                    logger.info("MynztripConnector: Searching Round-Trip %s <-> %s | Dep: %s - Ret: %s", origin, dest, date, return_date)
                else:
                    logger.info("MynztripConnector: Searching One-Way %s -> %s on %s", origin, dest, date)

            search_payload = {
                "journeyType": journey_type,
                "adults": 1,
                "childs": 0,
                "infants": 0,
                "childrenAges": [],
                "class": "Economy",
                "currency": currency,
                "market_id": market_id,
                "fare_type": 1,
                "airline": None,
                "preferredCarriers": None,
                "routes": routes
            }

            air_results = None
            try:
                air_results = await page.evaluate("""
                    async (payload) => {
                        const response = await fetch('https://nztrip.my/api/b2c/air-search', {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/json',
                                'Accept': 'application/json, text/plain, */*',
                                'X-Requested-With': 'XMLHttpRequest'
                            },
                            body: JSON.stringify(payload)
                        });
                        
                        if (!response.ok) {
                            const text = await response.text();
                            return { flag: false, status: response.status, message: `HTTP ${response.status}: ${text}` };
                        }
                        return { flag: true, status: response.status, data: await response.json() };
                    }
                """, search_payload)
            except Exception as e:
                logger.error("MynztripConnector: JavaScript fetch failed during page.evaluate.", exc_info=True)
                await browser.close()
                return []
                
            if air_results and "status" in air_results:
                logger.info("MynztripConnector: _SEARCH_API returned HTTP status %s", air_results["status"])

            offers = []
            if air_results and air_results.get("flag"):
                data = air_results.get("data", {})
                
                if "data" in data and "AirSearchResponses" in data["data"]:
                    flights = data["data"].get("AirSearchResponses", [])
                else:
                    flights = data.get("AirSearchResponses", [])
                
                logger.info("MynztripConnector: Successfully found %d results", len(flights))
                if not flights:
                    logger.info("MynztripConnector: 0 results returned. Reason: API returned empty AirSearchResponses.")

                for i, f in enumerate(flights):
                    raw_airline = f.get("PlatingCarrierName")
                    airline = str(raw_airline).strip() if raw_airline else "Unknown airline"
                    
                    total_price_val = f.get("TotalPrice", 0)
                    
                    try:
                        total_price = Decimal(str(total_price_val))
                    except Exception:
                        total_price = Decimal("0")
                        
                    directions = f.get("Directions", [])
                    flight_numbers = []
                    stops = 0
                    dep_datetime = None
                    arr_datetime = None
                    duration_minutes = 0
                    
                    if directions and directions[0] and directions[0][0].get("Segments"):
                        first_direction_segments = directions[0][0]["Segments"]
                        stops = max(0, len(first_direction_segments) - 1)
                        for seg in first_direction_segments:
                            fl_num = f"{seg.get('AirlineCode', '')}{seg.get('FlightNumber', '')}"
                            flight_numbers.append(fl_num)
                        
                        first_seg = first_direction_segments[0]
                        last_seg = first_direction_segments[-1]
                        
                        try:
                            dep_datetime = datetime.fromisoformat(first_seg.get("Departure", ""))
                        except:
                            dep_datetime = datetime.now()
                            
                        try:
                            arr_datetime = datetime.fromisoformat(last_seg.get("Arrival", ""))
                        except:
                            arr_datetime = dep_datetime

                        dur_seconds = (arr_datetime - dep_datetime).total_seconds()
                        if dur_seconds > 0:
                            duration_minutes = int(dur_seconds // 60)
                            
                    else:
                        dep_datetime = datetime.now()
                        arr_datetime = datetime.now()

                    offer = RawFlightOffer(
                        source=self.name,
                        airline=airline,
                        flight_numbers=flight_numbers,
                        origin=routes[0]["origin"] if routes else origin,
                        destination=routes[0]["destination"] if routes else dest,
                        departure_at=dep_datetime,
                        arrival_at=arr_datetime,
                        stops=stops,
                        duration_minutes=duration_minutes,
                        cabin="Economy",
                        fare_brand=None,
                        baggage=None,
                        fare_rules=None,
                        base_price=total_price,
                        taxes=Decimal("0"),
                        fees=Decimal("0"),
                        total_price=total_price,
                        currency=currency or "MYR",
                        booking_url="https://mynztrip.com",
                        raw_payload=f,
                    )
                    offers.append(offer)
            else:
                err_msg = air_results.get("message") if air_results else "No Response / JS Error"
                logger.error("MynztripConnector: 0 results returned. Reason: %s", err_msg)

            await browser.close()
            return offers
