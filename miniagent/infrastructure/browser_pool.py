"""Process-owned Playwright browser pool used by hot-reloaded skill tools."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from miniagent.core.constants import BROWSER_IDLE_TIMEOUT_SECONDS
from miniagent.infrastructure.trace_events import (
    EVENT_BROWSER_CLOSE,
    EVENT_BROWSER_CREATE,
    EVENT_BROWSER_REUSE,
)
from miniagent.infrastructure.tracing import emit_trace

_logger = logging.getLogger(__name__)

_browser: Any | None = None
_playwright: Any | None = None
_playwright_context: Any | None = None
_browser_loop: asyncio.AbstractEventLoop | None = None
_browser_lock = asyncio.Lock()
_browser_last_used = 0.0


async def _close_owned_resources(*, idle_seconds: int | None = None) -> None:
    """Close Chromium and its Playwright driver as one owned resource."""
    global _browser, _playwright, _playwright_context, _browser_loop

    browser = _browser
    playwright = _playwright
    playwright_context = _playwright_context
    _browser = None
    _playwright = None
    _playwright_context = None
    _browser_loop = None

    if browser is not None:
        try:
            await browser.close()
            event: dict[str, Any] = {"type": EVENT_BROWSER_CLOSE}
            if idle_seconds is not None:
                event["idle_seconds"] = idle_seconds
            emit_trace(event)
        except Exception as error:
            _logger.debug("关闭浏览器实例失败: %s", error)

    try:
        stop = getattr(playwright, "stop", None)
        if callable(stop):
            await stop()
        elif playwright_context is not None:
            exit_context = getattr(playwright_context, "__aexit__", None)
            if callable(exit_context):
                await exit_context(None, None, None)
    except Exception as error:
        _logger.debug("停止 Playwright driver 失败: %s", error)


async def get_browser_instance() -> Any:
    """Return a lazily created browser, recycling idle or cross-loop owners."""
    global _browser, _playwright, _playwright_context, _browser_loop, _browser_last_used

    loop = asyncio.get_running_loop()
    async with _browser_lock:
        now = time.monotonic()
        idle_seconds = now - _browser_last_used
        if _browser is not None and (
            _browser_loop is not loop or idle_seconds > float(BROWSER_IDLE_TIMEOUT_SECONDS)
        ):
            await _close_owned_resources(idle_seconds=int(max(0.0, idle_seconds)))
            _logger.info("浏览器实例已回收（空闲超时或事件循环切换）")

        if _browser is None:
            try:
                from playwright.async_api import async_playwright

                started_at = time.perf_counter()
                playwright_context = async_playwright()
                start = getattr(playwright_context, "start", None)
                if callable(start):
                    playwright = await start()
                else:
                    playwright = await playwright_context.__aenter__()

                _playwright = playwright
                _playwright_context = playwright_context
                _browser_loop = loop
                _browser = await playwright.chromium.launch(
                    headless=True,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--disable-dev-shm-usage",
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                    ],
                )
                duration_ms = int((time.perf_counter() - started_at) * 1000)
                emit_trace(
                    {
                        "type": EVENT_BROWSER_CREATE,
                        "duration_ms": duration_ms,
                    }
                )
                _logger.info("全局浏览器实例已创建（复用模式），耗时 %dms", duration_ms)
            except Exception:
                await _close_owned_resources()
                raise
        else:
            emit_trace(
                {
                    "type": EVENT_BROWSER_REUSE,
                    "idle_seconds": int(max(0.0, idle_seconds)),
                }
            )

        _browser_last_used = time.monotonic()
        return _browser


async def close_browser_pool() -> None:
    """Close the browser and driver; safe to call repeatedly during shutdown."""
    async with _browser_lock:
        if _browser is not None or _playwright is not None or _playwright_context is not None:
            await _close_owned_resources()


__all__ = ["close_browser_pool", "get_browser_instance"]
