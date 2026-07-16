from __future__ import annotations

import asyncio
import io
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from miniagent.assistant.feishu import drive_client
from miniagent.assistant.feishu.types import FeishuConfig


@pytest.mark.asyncio
async def test_shared_http_client_reused_and_closed(monkeypatch) -> None:
    client = MagicMock()
    client.aclose = AsyncMock()
    factory = MagicMock(return_value=client)
    drive_client.reset_http_client()
    monkeypatch.setattr(drive_client.httpx, "AsyncClient", factory)

    assert drive_client._get_http_client() is client
    assert drive_client._get_http_client() is client
    factory.assert_called_once_with(timeout=30.0)

    await drive_client.close_http_client()

    client.aclose.assert_awaited_once()
    assert drive_client._http_client is None


def test_concurrent_sync_token_misses_share_one_fetch(monkeypatch) -> None:
    config = FeishuConfig(app_id="sync-app", app_secret="secret")
    calls = 0
    calls_lock = threading.Lock()

    def fetch(_config: FeishuConfig) -> str:
        nonlocal calls
        with calls_lock:
            calls += 1
        time.sleep(0.03)
        return "sync-token"

    drive_client.clear_token_cache()
    monkeypatch.setattr(drive_client, "_fetch_tenant_access_token_sync", fetch)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(
                drive_client._get_cached_tenant_token,
                [config] * 8,
            )
        )

    assert results == ["sync-token"] * 8
    assert calls == 1
    drive_client.clear_token_cache()


@pytest.mark.asyncio
async def test_concurrent_async_token_misses_share_one_fetch(monkeypatch) -> None:
    config = FeishuConfig(app_id="async-app", app_secret="secret")
    calls = 0
    gate = asyncio.Event()

    async def fetch(_config: FeishuConfig) -> str:
        nonlocal calls
        calls += 1
        await gate.wait()
        return "async-token"

    drive_client.clear_token_cache()
    monkeypatch.setattr(drive_client, "_fetch_tenant_access_token_async", fetch)
    tasks = [
        asyncio.create_task(drive_client._get_cached_tenant_token_async(config))
        for _ in range(8)
    ]
    await asyncio.sleep(0)
    gate.set()
    results = await asyncio.gather(*tasks)

    assert results == ["async-token"] * 8
    assert calls == 1
    drive_client.clear_token_cache()
@pytest.mark.parametrize(
    "url",
    [
        "http://open.feishu.cn/open-apis/test",
        "https://example.invalid/open-apis/test",
        "file:///etc/passwd",
    ],
)
def test_http_helpers_reject_non_feishu_urls(url: str) -> None:
    """同步与异步 HTTP helper 只接受内置飞书开放平台 HTTPS 主机。"""
    with pytest.raises(ValueError, match="不允许"):
        drive_client._validate_feishu_api_url(url)


@pytest.mark.asyncio
async def test_async_http_request_success_get_and_post(monkeypatch) -> None:
    client = MagicMock()
    client.get = AsyncMock(
        return_value=httpx.Response(
            200,
            text='{"code": 0, "method": "get"}',
            request=httpx.Request("GET", drive_client._root_folder_meta_url()),
        )
    )
    client.post = AsyncMock(
        return_value=httpx.Response(
            200,
            text='{"code": 0, "method": "post"}',
            request=httpx.Request("POST", drive_client._tenant_token_url()),
        )
    )
    monkeypatch.setattr(drive_client, "_get_http_client", lambda: client)

    get_result = await drive_client._async_http_request(
        "GET", drive_client._root_folder_meta_url(), headers={"X-Test": "1"}
    )
    post_result = await drive_client._async_http_request(
        "POST", drive_client._tenant_token_url(), payload={"a": 1}
    )

    assert get_result["method"] == "get"
    assert post_result["method"] == "post"
    assert client.get.await_args.kwargs["headers"]["X-Test"] == "1"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 503])
async def test_async_http_request_status_errors(monkeypatch, status: int) -> None:
    url = drive_client._tenant_token_url()
    response = httpx.Response(
        status,
        text="service failed",
        request=httpx.Request("POST", url),
    )
    client = MagicMock()
    client.post = AsyncMock(return_value=response)
    monkeypatch.setattr(drive_client, "_get_http_client", lambda: client)
    sleep = AsyncMock()
    monkeypatch.setattr(drive_client.asyncio, "sleep", sleep)

    with pytest.raises(RuntimeError, match=f"HTTP {status}"):
        await drive_client._async_http_request(
            "POST", url, max_retries=2, backoff_factor=0
        )

    assert client.post.await_count == (1 if status < 500 else 2)
    assert sleep.await_count == (0 if status < 500 else 1)


@pytest.mark.asyncio
async def test_async_http_request_network_and_json_errors(monkeypatch) -> None:
    url = drive_client._root_folder_meta_url()
    client = MagicMock()
    client.get = AsyncMock(
        side_effect=httpx.RequestError("offline", request=httpx.Request("GET", url))
    )
    monkeypatch.setattr(drive_client, "_get_http_client", lambda: client)
    monkeypatch.setattr(drive_client.asyncio, "sleep", AsyncMock())

    with pytest.raises(RuntimeError, match="network error"):
        await drive_client._async_http_request("GET", url, max_retries=2)

    client.get = AsyncMock(
        return_value=httpx.Response(
            200,
            text="not-json",
            request=httpx.Request("GET", url),
        )
    )
    with pytest.raises(RuntimeError, match="invalid JSON"):
        await drive_client._async_http_request("GET", url, max_retries=1)


def test_sync_http_request_success_and_errors(monkeypatch) -> None:
    class Response:
        def __init__(self, body: bytes) -> None:
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self) -> bytes:
            return self.body

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda _request, timeout: Response(b'{"code": 0, "ok": true}'),
    )
    result = drive_client._http_request(
        "POST",
        drive_client._tenant_token_url(),
        payload={"a": 1},
        headers={"X-Test": "1"},
    )
    assert result["ok"] is True

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda _request, timeout: Response(b"not-json"),
    )
    with pytest.raises(RuntimeError, match="invalid JSON"):
        drive_client._http_request("GET", drive_client._root_folder_meta_url())

    from urllib.error import HTTPError, URLError

    def http_error(_request, timeout):
        raise HTTPError(
            drive_client._root_folder_meta_url(),
            403,
            "forbidden",
            {},
            io.BytesIO(b"denied"),
        )

    monkeypatch.setattr("urllib.request.urlopen", http_error)
    with pytest.raises(RuntimeError, match="HTTP 403"):
        drive_client._http_request("GET", drive_client._root_folder_meta_url())

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda _request, timeout: (_ for _ in ()).throw(URLError("offline")),
    )
    with pytest.raises(RuntimeError, match="network error"):
        drive_client._http_request("GET", drive_client._root_folder_meta_url())


@pytest.mark.asyncio
async def test_async_token_and_root_meta_validation(monkeypatch) -> None:
    config = FeishuConfig(app_id="validation-app", app_secret="secret")
    request = AsyncMock(return_value={"code": 0, "tenant_access_token": " token "})
    monkeypatch.setattr(drive_client, "_async_http_request", request)
    assert await drive_client._fetch_tenant_access_token_async(config) == "token"

    for payload in (
        {"code": "bad", "msg": "invalid"},
        {"code": 1, "msg": "denied"},
        {"code": 0, "tenant_access_token": ""},
    ):
        request.return_value = payload
        with pytest.raises(RuntimeError):
            await drive_client._fetch_tenant_access_token_async(config)

    monkeypatch.setattr(
        drive_client, "_get_cached_tenant_token_async", AsyncMock(return_value="token")
    )
    request.return_value = {"code": 0, "data": {"token": " root "}}
    assert await drive_client.get_root_folder_meta_async(config) == "root"

    for payload in (
        {"code": None, "msg": "bad"},
        {"code": 0, "data": None},
        {"code": 0, "data": {"token": ""}},
    ):
        request.return_value = payload
        with pytest.raises(RuntimeError):
            await drive_client.get_root_folder_meta_async(config)


def test_sync_token_and_root_meta_missing_fields(monkeypatch) -> None:
    config = FeishuConfig(app_id="sync-validation-app", app_secret="secret")
    request = MagicMock(return_value={"code": 0, "tenant_access_token": ""})
    monkeypatch.setattr(drive_client, "_http_request", request)
    with pytest.raises(RuntimeError, match="empty tenant_access_token"):
        drive_client._fetch_tenant_access_token_sync(config)

    request.return_value = {"code": "invalid", "msg": "bad"}
    with pytest.raises(RuntimeError, match="code="):
        drive_client._fetch_tenant_access_token_sync(config)

    monkeypatch.setattr(drive_client, "_get_cached_tenant_token", lambda _config: "token")
    request.return_value = {"code": 0, "data": None}
    with pytest.raises(RuntimeError, match="missing data"):
        drive_client.get_root_folder_meta(config)
    request.return_value = {"code": 0, "data": {"token": ""}}
    with pytest.raises(RuntimeError, match="empty token"):
        drive_client.get_root_folder_meta(config)
