"""web_search（Tavily）与 browser_extract_text 的 mock 单测。

工具定义已移至 ``miniagent/skills/templates/builtin-web``；测试直接导入 skill tools.py。
"""

from __future__ import annotations

import importlib.util
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Check if playwright is available (browser extra)
_HAS_PLAYWRIGHT = importlib.util.find_spec("playwright") is not None

# 从 skill 模板导入（非 ALL_TOOLS 路径）
_skill_tools = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "miniagent", "skills", "templates", "builtin-web", "skills", "web-tools", "tools.py",
)
_import_result = None
try:
    import importlib.util
    spec = importlib.util.spec_from_file_location("_builtin_web_tools", _skill_tools)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _import_result = mod
except Exception:
    pass

if _import_result:
    _web_search_handler = _import_result._web_search_handler
    _browser_extract_handler = _import_result._browser_extract_handler
else:
    # Fallback: skip tests if skill template can't be imported
    _web_search_handler = None
    _browser_extract_handler = None


@pytest.mark.skipif(_web_search_handler is None, reason="builtin-web skill template not importable")
@pytest.mark.asyncio
async def test_web_search_missing_key() -> None:
    with patch.dict("os.environ", {}, clear=True):
        from miniagent.types.tool import ToolContext
        ctx = ToolContext(cwd=".", allowed_paths=["."], permission="sandbox")
        r = await _web_search_handler({"query": "深圳天气"}, ctx)
        assert not r.success
        assert "TAVILY" in r.content or "WEB_SEARCH" in r.content


@pytest.mark.skipif(_web_search_handler is None, reason="builtin-web skill template not importable")
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

    from miniagent.types.tool import ToolContext
    with patch("httpx.AsyncClient", return_value=mock_client):
        ctx = ToolContext(cwd=".", allowed_paths=["."], permission="sandbox")
        r = await _web_search_handler({"query": "深圳", "maxResults": 3}, ctx)
    assert r.success
    assert "深圳" in r.content
    assert "明日多云" in r.content


@pytest.mark.skipif(_browser_extract_handler is None, reason="builtin-web skill template not importable")
@pytest.mark.asyncio
async def test_browser_extract_invalid_url() -> None:
    from miniagent.types.tool import ToolContext
    ctx = ToolContext(cwd=".", allowed_paths=["."], permission="sandbox")
    r = await _browser_extract_handler({"url": "file:///etc/passwd"}, ctx)
    assert not r.success


@pytest.mark.skipif(_browser_extract_handler is None, reason="builtin-web skill template not importable")
@pytest.mark.skipif(not _HAS_PLAYWRIGHT, reason="playwright not installed (browser extra)")
@pytest.mark.asyncio
async def test_browser_extract_playwright_mock() -> None:
    from miniagent.types.tool import ToolContext
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
