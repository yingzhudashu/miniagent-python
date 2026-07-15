"""web_search（Tavily）与 browser_extract_text 的 mock 单测。

工具定义位于 ``miniagent/assistant/skills/templates/builtin-web``；测试直接导入 skill tools.py。
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
    "miniagent",
    "assistant",
    "skills",
    "templates",
    "builtin-web",
    "skills",
    "web-tools",
    "tools.py",
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
    _fetch_url_handler = _import_result._fetch_url_handler
    _cleanup_browser = _import_result._cleanup_browser
else:
    # Fallback: skip tests if skill template can't be imported
    _web_search_handler = None
    _browser_extract_handler = None
    _fetch_url_handler = None
    _cleanup_browser = None


class _StreamResponse:
    """用于下载工具测试的最小异步流响应。"""

    def __init__(self, chunks: list[bytes], *, content_type: str = "application/octet-stream"):
        self._chunks = chunks
        self.headers = {"content-type": content_type}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def raise_for_status(self) -> None:
        return None

    async def aiter_bytes(self, *, chunk_size: int):
        assert chunk_size == 65536
        for chunk in self._chunks:
            yield chunk


class _DownloadClient:
    def __init__(self, chunks: list[bytes], *, length: int = 0, disposition: str = ""):
        self.chunks = chunks
        self.length = length
        self.disposition = disposition

    async def head(self, *_args, **_kwargs):
        return MagicMock(
            headers={
                "content-length": str(self.length),
                "content-type": "application/pdf",
                "content-disposition": self.disposition,
            }
        )

    def stream(self, *_args, **_kwargs):
        return _StreamResponse(self.chunks, content_type="application/pdf")


@pytest.mark.skipif(_import_result is None, reason="builtin-web skill template not importable")
@pytest.mark.asyncio
async def test_download_file_validates_url_and_head_size(tmp_path) -> None:
    from miniagent.agent.types.tool import ToolContext

    ctx = ToolContext(cwd=str(tmp_path), allowed_paths=[str(tmp_path)], permission="sandbox")
    invalid = await _import_result._download_file_handler({"url": "file:///etc/passwd"}, ctx)
    assert not invalid.success

    client = _DownloadClient([], length=2 * 1024 * 1024)
    with patch.object(_import_result, "get_shared_httpx_client", AsyncMock(return_value=client)):
        too_large = await _import_result._download_file_handler(
            {"url": "https://example.test/file.bin", "max_size_mb": 1}, ctx
        )
    assert not too_large.success and "文件过大" in too_large.content


@pytest.mark.skipif(_import_result is None, reason="builtin-web skill template not importable")
@pytest.mark.asyncio
async def test_download_file_streams_and_sanitizes_disposition(tmp_path) -> None:
    from miniagent.agent.types.tool import ToolContext

    ctx = ToolContext(cwd=str(tmp_path), allowed_paths=[str(tmp_path)], permission="sandbox")
    client = _DownloadClient(
        [b"abc", b"def"],
        length=6,
        disposition='attachment; filename="../safe.pdf"',
    )
    with patch.object(_import_result, "get_shared_httpx_client", AsyncMock(return_value=client)):
        result = await _import_result._download_file_handler(
            {"url": "https://example.test/original.bin"}, ctx
        )
    assert result.success and result.meta["size"] == 6
    assert result.meta["filename"] == "safe.pdf"
    assert (tmp_path / "safe.pdf").read_bytes() == b"abcdef"


@pytest.mark.skipif(_import_result is None, reason="builtin-web skill template not importable")
@pytest.mark.asyncio
async def test_download_file_stream_limit_removes_partial_file(tmp_path) -> None:
    from miniagent.agent.types.tool import ToolContext

    ctx = ToolContext(cwd=str(tmp_path), allowed_paths=[str(tmp_path)], permission="sandbox")
    client = _DownloadClient([b"x" * (1024 * 1024 + 1)], length=0)
    with patch.object(_import_result, "get_shared_httpx_client", AsyncMock(return_value=client)):
        result = await _import_result._download_file_handler(
            {"url": "https://example.test/huge.bin", "max_size_mb": 1}, ctx
        )
    assert not result.success and "超过限制" in result.content
    assert not (tmp_path / "huge.bin").exists()


@pytest.mark.skipif(_import_result is None, reason="builtin-web skill template not importable")
@pytest.mark.asyncio
async def test_download_probe_failure_stream_error_and_urllib_fallback(tmp_path, monkeypatch) -> None:
    from miniagent.agent.types.tool import ToolContext

    class BrokenHeadClient(_DownloadClient):
        async def head(self, *_args, **_kwargs):
            raise RuntimeError("head failed")

    client = BrokenHeadClient([b"ok"])
    length, content_type, filename, path = await _import_result._probe_download(
        client, "https://example.test/a", {}, str(tmp_path), "a.bin", 1
    )
    assert length == 0 and content_type == "application/octet-stream"
    assert filename == "a.bin" and path.endswith("a.bin")

    ctx = ToolContext(cwd=str(tmp_path), allowed_paths=[str(tmp_path)], permission="sandbox")
    with patch.object(
        _import_result,
        "get_shared_httpx_client",
        AsyncMock(side_effect=ImportError("httpx missing")),
    ), patch.object(
        _import_result,
        "_urllib_download",
        AsyncMock(return_value=(3, "text/plain")),
    ):
        result = await _import_result._download_file_handler(
            {"url": "https://example.test/a.txt"}, ctx
        )
    assert result.success and result.meta["size"] == 3

    failing = _DownloadClient([])
    failing.stream = MagicMock(side_effect=RuntimeError("stream failed"))
    partial = tmp_path / "partial.bin"
    partial.write_bytes(b"partial")
    with patch.object(_import_result, "_download_target", return_value=("partial.bin", str(partial))), patch.object(
        _import_result, "get_shared_httpx_client", AsyncMock(return_value=failing)
    ):
        result = await _import_result._download_file_handler(
            {"url": "https://example.test/a"}, ctx
        )
    assert not result.success and not partial.exists()


@pytest.mark.skipif(_web_search_handler is None, reason="builtin-web skill template not importable")
@pytest.mark.asyncio
async def test_web_search_missing_key() -> None:
    with patch.dict("os.environ", {}, clear=True):
        from miniagent.agent.types.tool import ToolContext

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
    mock_client.is_closed = False
    mock_client.post = AsyncMock(return_value=mock_resp)
    mock_client.aclose = AsyncMock()

    from miniagent.agent.types.tool import ToolContext

    with patch("httpx.AsyncClient", return_value=mock_client):
        ctx = ToolContext(cwd=".", allowed_paths=["."], permission="sandbox")
        r = await _web_search_handler({"query": "深圳", "maxResults": 3}, ctx)
    assert r.success
    assert "深圳" in r.content
    assert "明日多云" in r.content


@pytest.mark.skipif(_fetch_url_handler is None, reason="builtin-web skill template not importable")
@pytest.mark.asyncio
async def test_fetch_url_reuses_shared_http_client() -> None:
    from miniagent.agent.types.tool import ToolContext
    from miniagent.assistant.infrastructure.httpx_pool import close_shared_httpx_clients

    await close_shared_httpx_clients()
    response = MagicMock()
    response.text = "<html><body>Hello</body></html>"
    response.raise_for_status = MagicMock()
    client = AsyncMock()
    client.is_closed = False
    client.get = AsyncMock(return_value=response)
    client.aclose = AsyncMock()

    with patch("httpx.AsyncClient", return_value=client) as factory:
        ctx = ToolContext(cwd=".", allowed_paths=["."], permission="sandbox")
        first = await _fetch_url_handler({"url": "https://example.com"}, ctx)
        second = await _fetch_url_handler({"url": "https://example.com/2"}, ctx)

    assert first.success and second.success
    assert factory.call_count == 1
    assert client.get.await_count == 2
    await close_shared_httpx_clients()


@pytest.mark.skipif(_cleanup_browser is None, reason="builtin-web skill template not importable")
@pytest.mark.asyncio
async def test_browser_cleanup_closes_browser_and_playwright_driver() -> None:
    from miniagent.assistant.infrastructure import browser_pool

    browser = AsyncMock()
    playwright = AsyncMock()
    browser_pool._browser = browser
    browser_pool._playwright = playwright
    browser_pool._playwright_context = None

    await _cleanup_browser()

    browser.close.assert_awaited_once()
    playwright.stop.assert_awaited_once()
    assert browser_pool._browser is None
    assert browser_pool._playwright is None


@pytest.mark.skipif(
    _browser_extract_handler is None, reason="builtin-web skill template not importable"
)
@pytest.mark.asyncio
async def test_browser_extract_invalid_url() -> None:
    from miniagent.agent.types.tool import ToolContext

    ctx = ToolContext(cwd=".", allowed_paths=["."], permission="sandbox")
    r = await _browser_extract_handler({"url": "file:///etc/passwd"}, ctx)
    assert not r.success


@pytest.mark.skipif(
    _browser_extract_handler is None, reason="builtin-web skill template not importable"
)
@pytest.mark.skipif(not _HAS_PLAYWRIGHT, reason="playwright not installed (browser extra)")
@pytest.mark.asyncio
async def test_browser_extract_playwright_mock() -> None:
    from miniagent.assistant.infrastructure.browser_pool import close_browser_pool

    await close_browser_pool()
    from miniagent.agent.types.tool import ToolContext

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
        await close_browser_pool()
    assert r.success
    assert "页面正文" in r.content
