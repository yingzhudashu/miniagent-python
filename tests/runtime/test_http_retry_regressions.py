"""Focused regressions migrated from test_diff_gate_new_modules.py."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from miniagent.assistant.infrastructure import http_retry


def _response(status: int, text: str = "ok") -> httpx.Response:
    return httpx.Response(status, text=text, request=httpx.Request("GET", "https://x"))

@pytest.mark.asyncio
async def test_http_retry_methods_success_and_json_helpers() -> None:
    post = AsyncMock(return_value=_response(200, '{"post": true}'))
    get = AsyncMock(return_value=_response(200, '{"get": true}'))
    request = AsyncMock(return_value=_response(200, "custom"))
    client = SimpleNamespace(post=post, get=get, request=request)

    assert await http_retry.async_http_post_json_with_retry(
        client, "https://x", payload={"x": 1}, headers={"A": "B"}, max_retries=1
    ) == {"post": True}
    assert await http_retry.async_http_get_json_with_retry(
        client, "https://x", headers={"A": "B"}, max_retries=1
    ) == {"get": True}
    response = await http_retry.async_http_request_with_retry(
        client, "PATCH", "https://x", timeout=2, max_retries=1
    )
    assert response.text == "custom"
    request.assert_awaited_once_with("PATCH", "https://x", timeout=2)

@pytest.mark.asyncio
async def test_http_retry_status_timeout_network_and_zero_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleep = AsyncMock()
    monkeypatch.setattr(http_retry.asyncio, "sleep", sleep)
    bad_request = SimpleNamespace(
        get=AsyncMock(return_value=_response(400, "bad")),
        post=AsyncMock(),
        request=AsyncMock(),
    )
    with pytest.raises(RuntimeError, match="HTTP 400"):
        await http_retry.async_http_request_with_retry(
            bad_request, "GET", "https://x", max_retries=2
        )
    sleep.assert_not_awaited()

    server = SimpleNamespace(
        get=AsyncMock(side_effect=[_response(503, "busy"), _response(200)]),
        post=AsyncMock(),
        request=AsyncMock(),
    )
    assert (await http_retry.async_http_request_with_retry(
        server, "GET", "https://x", max_retries=2, backoff_factor=0
    )).status_code == 200
    with pytest.raises(RuntimeError, match="重试1次后"):
        await http_retry.async_http_request_with_retry(
            SimpleNamespace(get=AsyncMock(return_value=_response(500)), post=AsyncMock(), request=AsyncMock()),
            "GET", "https://x", max_retries=1,
        )

    for error, fragment in (
        (httpx.TimeoutException("late"), "请求超时"),
        (httpx.RequestError("offline"), "网络请求失败"),
    ):
        client = SimpleNamespace(get=AsyncMock(side_effect=error), post=AsyncMock(), request=AsyncMock())
        with pytest.raises(RuntimeError, match=fragment):
            await http_retry.async_http_request_with_retry(
                client, "GET", "https://x", max_retries=1
            )
    with pytest.raises(RuntimeError, match="未执行任何请求"):
        await http_retry.async_http_request_with_retry(
            bad_request, "GET", "https://x", max_retries=0
        )
