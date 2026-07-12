"""Stable browser pool ownership tests for hot-reloaded web skills."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from miniagent.infrastructure import browser_pool


@pytest.mark.asyncio
async def test_browser_pool_reuses_browser_and_closes_driver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await browser_pool.close_browser_pool()
    browser = AsyncMock()
    playwright = SimpleNamespace(
        chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)),
        stop=AsyncMock(),
    )
    context = SimpleNamespace(start=AsyncMock(return_value=playwright))
    async_api = ModuleType("playwright.async_api")
    async_api.async_playwright = lambda: context  # type: ignore[attr-defined]
    package = ModuleType("playwright")
    package.async_api = async_api  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright", package)
    monkeypatch.setitem(sys.modules, "playwright.async_api", async_api)

    first = await browser_pool.get_browser_instance()
    second = await browser_pool.get_browser_instance()

    assert first is browser
    assert second is browser
    playwright.chromium.launch.assert_awaited_once()

    await browser_pool.close_browser_pool()
    browser.close.assert_awaited_once()
    playwright.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_browser_pool_creation_failure_stops_started_driver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await browser_pool.close_browser_pool()
    playwright = SimpleNamespace(
        chromium=SimpleNamespace(launch=AsyncMock(side_effect=RuntimeError("launch failed"))),
        stop=AsyncMock(),
    )
    context = SimpleNamespace(start=AsyncMock(return_value=playwright))
    async_api = ModuleType("playwright.async_api")
    async_api.async_playwright = lambda: context  # type: ignore[attr-defined]
    package = ModuleType("playwright")
    package.async_api = async_api  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright", package)
    monkeypatch.setitem(sys.modules, "playwright.async_api", async_api)

    with pytest.raises(RuntimeError, match="launch failed"):
        await browser_pool.get_browser_instance()

    playwright.stop.assert_awaited_once()
    assert browser_pool._browser is None
    assert browser_pool._playwright is None
