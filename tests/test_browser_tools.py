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
