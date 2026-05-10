"""web_search（Tavily）与 browser_extract_text 的 mock 单测。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from miniagent.tools.web import _browser_extract_handler, _web_search_handler
from miniagent.types.tool import ToolContext


@pytest.mark.asyncio
async def test_web_search_missing_key() -> None:
    with patch.dict("os.environ", {}, clear=True):
        ctx = ToolContext(cwd=".", allowed_paths=["."], permission="sandbox")
        r = await _web_search_handler({"query": "深圳天气"}, ctx)
        assert not r.success
        assert "TAVILY" in r.content or "WEB_SEARCH" in r.content


@pytest.mark.asyncio
async def test_web_search_success_mock(monkeypatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(
        return_value={
            "answer": "明日多云",
            "results": [{"title": "天气网", "url": "https://example.com", "content": "摘要"}],
        }
    )

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client):
        ctx = ToolContext(cwd=".", allowed_paths=["."], permission="sandbox")
        r = await _web_search_handler({"query": "深圳", "maxResults": 3}, ctx)
    assert r.success
    assert "深圳" in r.content
    assert "明日多云" in r.content


@pytest.mark.asyncio
async def test_browser_extract_invalid_url() -> None:
    ctx = ToolContext(cwd=".", allowed_paths=["."], permission="sandbox")
    r = await _browser_extract_handler({"url": "file:///etc/passwd"}, ctx)
    assert not r.success


@pytest.mark.asyncio
async def test_browser_extract_playwright_mock() -> None:
    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.inner_text = AsyncMock(return_value="页面正文")

    mock_browser = AsyncMock()
    mock_browser.new_page = AsyncMock(return_value=mock_page)
    mock_browser.close = AsyncMock()

    fake_p = MagicMock()
    fake_p.chromium.launch = AsyncMock(return_value=mock_browser)

    class _CM:
        async def __aenter__(self):
            return fake_p

        async def __aexit__(self, *a):
            return None

    with patch("playwright.async_api.async_playwright", lambda: _CM()):
        ctx = ToolContext(cwd=".", allowed_paths=["."], permission="sandbox")
        r = await _browser_extract_handler({"url": "https://example.com"}, ctx)
    assert r.success
    assert "页面正文" in r.content
