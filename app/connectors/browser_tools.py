from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.connectors.base import ConnectorExecutionError


@dataclass(slots=True)
class BrowserCard:
    text: str
    link: str | None


async def scrape_cards(
    *,
    url: str,
    card_selector: str,
    link_selector: str,
    wait_ms: int,
    max_cards: int,
    headless: bool,
    timeout_ms: int,
) -> list[BrowserCard]:
    try:
        return await _scrape_cards_async(
            url=url,
            card_selector=card_selector,
            link_selector=link_selector,
            wait_ms=wait_ms,
            max_cards=max_cards,
            headless=headless,
            timeout_ms=timeout_ms,
        )
    except ModuleNotFoundError as exc:
        raise ConnectorExecutionError(
            "Playwright is required for browser connectors. Install dependencies first."
        ) from exc
    except NotImplementedError:
        # Windows selector event loop does not support subprocess APIs used by Playwright async.
        return await asyncio.to_thread(
            _scrape_cards_sync,
            url,
            card_selector,
            link_selector,
            wait_ms,
            max_cards,
            headless,
            timeout_ms,
        )


async def _scrape_cards_async(
    *,
    url: str,
    card_selector: str,
    link_selector: str,
    wait_ms: int,
    max_cards: int,
    headless: bool,
    timeout_ms: int,
) -> list[BrowserCard]:
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError
    from playwright.async_api import async_playwright

    cards: list[BrowserCard] = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=headless)
        context = await browser.new_context()
        page = await context.new_page()

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        except PlaywrightTimeoutError as exc:
            await browser.close()
            raise ConnectorExecutionError(f"Timed out loading page: {url}") from exc

        await page.wait_for_timeout(wait_ms)
        locator = page.locator(card_selector)
        count = await locator.count()

        if count == 0:
            body_text = (await page.locator("body").inner_text()).strip()
            await browser.close()
            return [BrowserCard(text=body_text, link=url)] if body_text else []

        for index in range(min(count, max_cards)):
            node = locator.nth(index)
            text = (await node.inner_text()).strip()
            link: str | None = None
            links = node.locator(link_selector)
            if await links.count() > 0:
                link = await links.nth(0).get_attribute("href")
            cards.append(BrowserCard(text=text, link=link))

        await browser.close()
    return cards


def _scrape_cards_sync(
    url: str,
    card_selector: str,
    link_selector: str,
    wait_ms: int,
    max_cards: int,
    headless: bool,
    timeout_ms: int,
) -> list[BrowserCard]:
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover - import path depends on environment
        raise ConnectorExecutionError(
            "Playwright sync API is required for browser connector fallback."
        ) from exc

    cards: list[BrowserCard] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        except PlaywrightTimeoutError as exc:
            browser.close()
            raise ConnectorExecutionError(f"Timed out loading page: {url}") from exc

        page.wait_for_timeout(wait_ms)
        locator = page.locator(card_selector)
        count = locator.count()
        if count == 0:
            body_text = page.locator("body").inner_text().strip()
            browser.close()
            return [BrowserCard(text=body_text, link=url)] if body_text else []

        for index in range(min(count, max_cards)):
            node = locator.nth(index)
            text = node.inner_text().strip()
            link: str | None = None
            links = node.locator(link_selector)
            if links.count() > 0:
                link = links.nth(0).get_attribute("href")
            cards.append(BrowserCard(text=text, link=link))
        browser.close()
    return cards
