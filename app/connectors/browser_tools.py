from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass

from app.connectors.base import ConnectorExecutionError


@dataclass(slots=True)
class BrowserCard:
    text: str
    link: str | None


_MAX_SCROLL_ROUNDS = 16
_SCROLL_WAIT_MS = 700
_STABLE_ROUNDS_TO_STOP = 3


async def scrape_cards(
    *,
    url: str,
    card_selector: str,
    link_selector: str,
    wait_ms: int,
    max_cards: int,
    headless: bool,
    timeout_ms: int,
    pre_collect_script: str | None = None,
) -> list[BrowserCard]:
    if _should_use_sync_playwright_fallback():
        return await asyncio.to_thread(
            _scrape_cards_sync,
            url,
            card_selector,
            link_selector,
            wait_ms,
            max_cards,
            headless,
            timeout_ms,
            pre_collect_script,
        )

    try:
        return await _scrape_cards_async(
            url=url,
            card_selector=card_selector,
            link_selector=link_selector,
            wait_ms=wait_ms,
            max_cards=max_cards,
            headless=headless,
            timeout_ms=timeout_ms,
            pre_collect_script=pre_collect_script,
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
            pre_collect_script,
        )


def _should_use_sync_playwright_fallback() -> bool:
    if not sys.platform.startswith("win"):
        return False
    try:
        loop_name = type(asyncio.get_running_loop()).__name__
    except RuntimeError:
        return False
    return "SelectorEventLoop" in loop_name


async def _scrape_cards_async(
    *,
    url: str,
    card_selector: str,
    link_selector: str,
    wait_ms: int,
    max_cards: int,
    headless: bool,
    timeout_ms: int,
    pre_collect_script: str | None,
) -> list[BrowserCard]:
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError
    from playwright.async_api import async_playwright

    cards: list[BrowserCard] = []
    seen: set[str] = set()
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
        await _run_pre_collect_script_async(page, pre_collect_script)
        if await page.locator(card_selector).count() == 0:
            body_text = (await page.locator("body").inner_text()).strip()
            await browser.close()
            return [BrowserCard(text=body_text, link=url)] if body_text else []

        stable_rounds = 0
        for _ in range(_MAX_SCROLL_ROUNDS + 1):
            before = len(cards)
            await _collect_visible_cards_async(
                page=page,
                card_selector=card_selector,
                link_selector=link_selector,
                cards=cards,
                seen=seen,
                max_cards=max_cards,
            )
            if len(cards) >= max_cards:
                break
            if len(cards) == before:
                stable_rounds += 1
            else:
                stable_rounds = 0
            if stable_rounds >= _STABLE_ROUNDS_TO_STOP:
                break
            await _scroll_page_async(page, card_selector)

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
    pre_collect_script: str | None,
) -> list[BrowserCard]:
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover - import path depends on environment
        raise ConnectorExecutionError(
            "Playwright sync API is required for browser connector fallback."
        ) from exc

    cards: list[BrowserCard] = []
    seen: set[str] = set()
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
        _run_pre_collect_script_sync(page, pre_collect_script)
        if page.locator(card_selector).count() == 0:
            body_text = page.locator("body").inner_text().strip()
            browser.close()
            return [BrowserCard(text=body_text, link=url)] if body_text else []

        stable_rounds = 0
        for _ in range(_MAX_SCROLL_ROUNDS + 1):
            before = len(cards)
            _collect_visible_cards_sync(
                page=page,
                card_selector=card_selector,
                link_selector=link_selector,
                cards=cards,
                seen=seen,
                max_cards=max_cards,
            )
            if len(cards) >= max_cards:
                break
            if len(cards) == before:
                stable_rounds += 1
            else:
                stable_rounds = 0
            if stable_rounds >= _STABLE_ROUNDS_TO_STOP:
                break
            _scroll_page_sync(page, card_selector)
        browser.close()
    return cards


async def _collect_visible_cards_async(
    *,
    page,
    card_selector: str,
    link_selector: str,
    cards: list[BrowserCard],
    seen: set[str],
    max_cards: int,
) -> None:
    locator = page.locator(card_selector)
    count = await locator.count()
    for index in range(count):
        if len(cards) >= max_cards:
            return
        node = locator.nth(index)
        text = (await node.inner_text()).strip()
        if not text:
            continue
        link: str | None = None
        links = node.locator(link_selector)
        if await links.count() > 0:
            link = await links.nth(0).get_attribute("href")
        _append_card(cards, seen, text, link)


def _collect_visible_cards_sync(
    *,
    page,
    card_selector: str,
    link_selector: str,
    cards: list[BrowserCard],
    seen: set[str],
    max_cards: int,
) -> None:
    locator = page.locator(card_selector)
    count = locator.count()
    for index in range(count):
        if len(cards) >= max_cards:
            return
        node = locator.nth(index)
        text = node.inner_text().strip()
        if not text:
            continue
        link: str | None = None
        links = node.locator(link_selector)
        if links.count() > 0:
            link = links.nth(0).get_attribute("href")
        _append_card(cards, seen, text, link)


def _append_card(cards: list[BrowserCard], seen: set[str], text: str, link: str | None) -> None:
    key = f"{text}\n{link or ''}"
    if key in seen:
        return
    seen.add(key)
    cards.append(BrowserCard(text=text, link=link))


async def _run_pre_collect_script_async(page, script: str | None) -> None:
    if not script:
        return
    try:
        await page.evaluate(script)
        await page.wait_for_timeout(450)
    except Exception:  # pragma: no cover - best effort per-site script
        return


def _run_pre_collect_script_sync(page, script: str | None) -> None:
    if not script:
        return
    try:
        page.evaluate(script)
        page.wait_for_timeout(450)
    except Exception:  # pragma: no cover - best effort per-site script
        return


async def _scroll_page_async(page, card_selector: str) -> None:
    await page.evaluate(
        """
        (selector) => {
          const card = document.querySelector(selector);
          let scrolled = false;
          let node = card ? card.parentElement : null;
          while (node) {
            const style = window.getComputedStyle(node);
            const canScroll = node.scrollHeight > node.clientHeight + 12;
            const overflowY = style.overflowY;
            if (canScroll && (overflowY === 'auto' || overflowY === 'scroll')) {
              node.scrollBy(0, Math.max(node.clientHeight, 900));
              scrolled = true;
              break;
            }
            node = node.parentElement;
          }
          if (!scrolled) {
            window.scrollBy(0, Math.max(window.innerHeight, 900));
          }
        }
        """,
        card_selector,
    )
    await page.wait_for_timeout(_SCROLL_WAIT_MS)
    await page.mouse.wheel(0, 2400)
    await page.wait_for_timeout(_SCROLL_WAIT_MS)


def _scroll_page_sync(page, card_selector: str) -> None:
    page.evaluate(
        """
        (selector) => {
          const card = document.querySelector(selector);
          let scrolled = false;
          let node = card ? card.parentElement : null;
          while (node) {
            const style = window.getComputedStyle(node);
            const canScroll = node.scrollHeight > node.clientHeight + 12;
            const overflowY = style.overflowY;
            if (canScroll && (overflowY === 'auto' || overflowY === 'scroll')) {
              node.scrollBy(0, Math.max(node.clientHeight, 900));
              scrolled = true;
              break;
            }
            node = node.parentElement;
          }
          if (!scrolled) {
            window.scrollBy(0, Math.max(window.innerHeight, 900));
          }
        }
        """,
        card_selector,
    )
    page.wait_for_timeout(_SCROLL_WAIT_MS)
    page.mouse.wheel(0, 2400)
    page.wait_for_timeout(_SCROLL_WAIT_MS)
