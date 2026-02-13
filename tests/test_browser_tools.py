from __future__ import annotations

import pytest

from app.connectors import browser_tools


@pytest.mark.asyncio
async def test_scrape_cards_falls_back_to_sync_when_async_not_supported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _async_fail(**kwargs):
        raise NotImplementedError()

    def _sync_ok(*args):
        return [browser_tools.BrowserCard(text="ok", link="https://example.com")]

    monkeypatch.setattr(browser_tools, "_scrape_cards_async", _async_fail)
    monkeypatch.setattr(browser_tools, "_scrape_cards_sync", _sync_ok)

    cards = await browser_tools.scrape_cards(
        url="https://example.com",
        card_selector="article",
        link_selector="a[href]",
        wait_ms=1,
        max_cards=1,
        headless=True,
        timeout_ms=1000,
    )

    assert len(cards) == 1
    assert cards[0].text == "ok"


@pytest.mark.asyncio
async def test_scrape_cards_uses_sync_fallback_for_windows_selector_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_called = {"value": False}
    sync_called = {"value": False}

    async def _async_path(**kwargs):
        async_called["value"] = True
        return []

    def _sync_path(*args):
        sync_called["value"] = True
        return [browser_tools.BrowserCard(text="sync", link=None)]

    monkeypatch.setattr(browser_tools, "_should_use_sync_playwright_fallback", lambda: True)
    monkeypatch.setattr(browser_tools, "_scrape_cards_async", _async_path)
    monkeypatch.setattr(browser_tools, "_scrape_cards_sync", _sync_path)

    cards = await browser_tools.scrape_cards(
        url="https://example.com",
        card_selector="article",
        link_selector="a[href]",
        wait_ms=1,
        max_cards=1,
        headless=True,
        timeout_ms=1000,
    )

    assert len(cards) == 1
    assert cards[0].text == "sync"
    assert sync_called["value"] is True
    assert async_called["value"] is False


def test_append_card_deduplicates_same_text_and_link() -> None:
    cards: list[browser_tools.BrowserCard] = []
    seen: set[str] = set()

    browser_tools._append_card(cards, seen, "Sample card", "https://example.com/a")
    browser_tools._append_card(cards, seen, "Sample card", "https://example.com/a")
    browser_tools._append_card(cards, seen, "Sample card", "https://example.com/b")

    assert len(cards) == 2
