import os
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

class BatikAirConnector(FlightConnector):
    name = "batikair"

    def __init__(self, settings: Any) -> None:
        super().__init__(settings)

    async def search(self, query: SearchCreateRequest) -> list[RawFlightOffer]:
        if not getattr(self.settings, "enable_browser_connectors", True):
            raise ConnectorExecutionError("Batikair connector requires browser connectors to be enabled")

        logger.info(f"BatikairConnector: Starting search for {query.origin} -> {query.destination}")
        
        return await asyncio.to_thread(
            run_in_proactor_loop,
            self._run_scraper(query)
        )

    async def _navigate_and_select(self, page, target_date_str, label_id, current_ui_date):
        await page.locator(label_id).click()
        await asyncio.sleep(0.5)

        target_date = datetime.strptime(target_date_str, "%Y-%m-%d")
        diff = (target_date.year - current_ui_date.year) * 12 + (target_date.month - current_ui_date.month)
        
        logger.info(f"[ACTION]: Navigating to {target_date_str} (Clicks: {diff})")

        if diff > 0:
            for _ in range(diff):
                await page.get_by_role("button", name="Next month (PageDown)").click()
                await asyncio.sleep(0.3)
        elif diff < 0:
            for _ in range(abs(diff)):
                await page.get_by_role("button", name="Previous month (PageUp)").click()
                await asyncio.sleep(0.3)

        specific_date_selector = f"td[title='{target_date_str}'].ant-picker-cell-in-view"
        
        td_elements = page.locator(specific_date_selector)
        td_count = await td_elements.count()
        if td_count > 0:
            logger.info(f"[LOG]: Found {td_count} matching td(s) for '{target_date_str}'")
            for i in range(td_count):
                try:
                    await td_elements.nth(i).click(timeout=2000)
                    logger.info(f"[LOG]: Successfully clicked td index {i}")
                    break
                except Exception:
                    logger.info(f"[LOG]: skipped td index {i} as it was not visible/interactable.")
        else:
            logger.warning(f"[LOG]: Could NOT find td for '{target_date_str}'")

    async def _scrape_flights(self, page, query: SearchCreateRequest) -> list[RawFlightOffer]:
        logger.info("\n[ACTION]: Scraping flight data from page...")
        
        try:
            await page.wait_for_selector("div.ant-row-space-between.gap-3", timeout=15000)
        except Exception:
            logger.warning("Timeout waiting for flight rows...")
        
        flight_rows = await page.locator("div.ant-row-space-between.gap-3").all()
        
        offers = []
        for row in flight_rows:
            try:
                semibold_count = await row.locator("span.font-semibold").count()
                if semibold_count == 0:
                    continue

                flight_no = (await row.locator("span.font-semibold").first.inner_text()).strip()
                
                times = await row.locator("span.text-2xl").all_inner_texts()
                dep_time = times[0] if len(times) > 0 else "N/A"
                arr_time = times[1] if len(times) > 1 else "N/A"

                economy_price_str = "N/A"
                eco_btn = row.locator("button:has-text('economy')")
                if await eco_btn.count() > 0:
                    economy_price_str = (await eco_btn.locator("p.m-0").inner_text()).replace("\n", " ").strip()

                business_price_str = "N/A"
                bus_btn = row.locator("button:has-text('business')")
                if await bus_btn.count() > 0:
                    bus_texts = await bus_btn.locator("p.m-0").all_inner_texts()
                    if bus_texts:
                        business_price_str = bus_texts[-1].replace("\n", " ").strip()

                def clean_price(price_str):
                    if price_str == "N/A":
                        return None
                    import re
                    match = re.search(r'[\d,]+(?:\.\d+)?', price_str)
                    if match:
                        return Decimal(match.group(0).replace(",", ""))
                    return None

                eco_price = clean_price(economy_price_str)
                dep_dt = datetime.combine(query.departure_date, datetime.strptime(dep_time, "%H:%M").time()) if dep_time != "N/A" else datetime.now()
                arr_dt = datetime.combine(query.departure_date, datetime.strptime(arr_time, "%H:%M").time()) if arr_time != "N/A" else datetime.now()
                
                if arr_dt < dep_dt:
                    arr_dt += timedelta(days=1)

                row_all_texts = await row.all_inner_texts()
                full_row_text = " ".join(row_all_texts).replace("\n", " ")
                
                stops_count = 0
                import re
                if "Direct" in full_row_text:
                    stops_count = 0
                else:
                    stop_match = re.search(r'(\d+)\s*Stop', full_row_text, re.IGNORECASE)
                    if stop_match:
                        stops_count = int(stop_match.group(1))

                if eco_price is not None:
                    offer = RawFlightOffer(
                        source=self.name,
                        airline="Batik Air",
                        flight_numbers=[flight_no],
                        origin=query.origin,
                        destination=query.destination,
                        departure_at=dep_dt,
                        arrival_at=arr_dt,
                        stops=stops_count,
                        duration_minutes=int((arr_dt - dep_dt).total_seconds() / 60) if dep_time != "N/A" and arr_time != "N/A" else 0,
                        cabin="Economy",
                        fare_brand=None,
                        baggage=None,
                        fare_rules=None,
                        base_price=eco_price,
                        taxes=Decimal("0"),
                        fees=Decimal("0"),
                        total_price=eco_price,
                        currency="MYR", 
                        booking_url="https://www.batikair.com.my/",
                        raw_payload={
                            "flight_no": flight_no,
                            "departure": dep_time,
                            "arrival": arr_time,
                            "economy": economy_price_str,
                            "business": business_price_str
                        },
                    )
                    offers.append(offer)
                    logger.info(f" > {flight_no} | {dep_time} -> {arr_time} | Eco: {economy_price_str}")
            
            except Exception as e:
                logger.error(f"Error parsing row: {e}")
                continue
                
        logger.info(f"BatikAirConnector: Successfully extracted {len(offers)} valid flight offer(s)")
        return offers

    async def _run_scraper(self, query: SearchCreateRequest) -> list[RawFlightOffer]:
        async with async_playwright() as p:
            headless = getattr(self.settings, "browser_headless", False)
            browser = await p.chromium.launch(headless=headless, slow_mo=500)
            page = await browser.new_page()
            
            url = "https://www.batikair.com.my/"
            await page.goto(url)

            trip_type = "Round trip" if query.return_date else "One way"
            await page.get_by_text(trip_type, exact=True).first.click()
            
            await page.get_by_role("combobox").nth(1).fill(query.origin)
            await page.keyboard.press("Enter")
            await page.get_by_role("combobox").nth(2).fill(query.destination)
            await page.keyboard.press("Enter")
            
            os_now = datetime.now()
            dep_date_str = query.departure_date.strftime("%Y-%m-%d")
            await self._navigate_and_select(page, dep_date_str, "#search_date_dep_0", os_now)

            if query.return_date:
                ret_date_str = query.return_date.strftime("%Y-%m-%d")
                
                auto_shifted_date_obj = query.departure_date + timedelta(days=7)
                auto_shifted_str = auto_shifted_date_obj.strftime("%Y-%m-%d")
        
                if ret_date_str != auto_shifted_str:
                    logger.info(f"[LOG] Target {ret_date_str} is not the default. Selecting manually...")
                    await self._navigate_and_select(page, ret_date_str, "#search_date_arr_0", auto_shifted_date_obj)
                else:
                    logger.info("[SMART SKIP] Arrival date is already 7 days after departure. No click needed.")
                    await asyncio.sleep(2.5)

            logger.info("\n[ACTION]: Searching...")
            await page.locator("#search_btn").click()
            
            logger.info("[LOG]: Waiting 12 seconds for results page...")
            await asyncio.sleep(12)

            offers = await self._scrape_flights(page, query)
            
            await browser.close()
            return offers