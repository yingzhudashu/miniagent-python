"""Lark SDK client cache concurrency and credential rotation tests."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from miniagent.agent.constants import FEISHU_SDK_CLIENT_CACHE_MAX_SIZE
from miniagent.assistant.feishu import lark_client
from miniagent.assistant.feishu.types import FeishuConfig


def test_concurrent_lark_client_misses_build_once(monkeypatch) -> None:
    lark_client.clear_client_cache()
    calls = 0
    calls_lock = threading.Lock()

    def create(_config: FeishuConfig) -> object:
        nonlocal calls
        with calls_lock:
            calls += 1
        time.sleep(0.02)
        return object()

    monkeypatch.setattr(lark_client, "_create_client", create)
    config = FeishuConfig(app_id="app", app_secret="secret")
    with ThreadPoolExecutor(max_workers=8) as pool:
        clients = list(pool.map(lark_client.build_client, [config] * 8))

    assert calls == 1
    assert all(client is clients[0] for client in clients)
    lark_client.clear_client_cache()


def test_lark_client_secret_rotation_replaces_same_app(monkeypatch) -> None:
    lark_client.clear_client_cache()
    monkeypatch.setattr(lark_client, "_create_client", lambda config: object())

    first = lark_client.build_client(FeishuConfig(app_id="app", app_secret="old"))
    second = lark_client.build_client(FeishuConfig(app_id="app", app_secret="new"))

    assert first is not second
    assert len(lark_client._client_cache) == 1
    assert all(
        "old" not in repr(key) and "new" not in repr(key) for key in lark_client._client_cache
    )


def test_lark_client_cache_is_bounded(monkeypatch) -> None:
    lark_client.clear_client_cache()
    monkeypatch.setattr(lark_client, "_create_client", lambda config: object())

    for index in range(FEISHU_SDK_CLIENT_CACHE_MAX_SIZE + 5):
        lark_client.build_client(FeishuConfig(app_id=f"app-{index}", app_secret="secret"))

    assert len(lark_client._client_cache) == FEISHU_SDK_CLIENT_CACHE_MAX_SIZE
    lark_client.clear_client_cache()
