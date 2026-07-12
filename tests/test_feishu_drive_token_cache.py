from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from miniagent.feishu import drive_client
from miniagent.feishu.types import FeishuConfig


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
