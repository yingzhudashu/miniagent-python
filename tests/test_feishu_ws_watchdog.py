"""飞书 WebSocket 会话监督与看门狗单测。"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from miniagent.feishu import ws_health
from miniagent.feishu.ws_health import (
    read_feishu_ws_health_config,
    supervise_feishu_ws_session,
    touch_ws_inbound_activity,
)


def _mock_ws_client(
    *,
    connected: bool = True,
    receive_task: asyncio.Task | None = None,
) -> MagicMock:
    client = MagicMock()
    client.connected = connected
    client.receive_task = receive_task
    client._disconnect = AsyncMock()
    return client


@pytest.mark.asyncio
async def test_supervise_returns_on_receive_task_done():
    async def recv_coro():
        return None

    receive_task = asyncio.create_task(recv_coro())
    await receive_task

    client = _mock_ws_client(receive_task=receive_task)
    shutdown = asyncio.Event()
    reason = await supervise_feishu_ws_session(client, shutdown_event=shutdown)
    assert reason.startswith("receive_loop_exit")
    end_reason, _ = ws_health.get_last_ws_session_end()
    assert end_reason == reason


@pytest.mark.asyncio
async def test_watchdog_reconnect_grace_sdk_auto(monkeypatch):
    monkeypatch.setenv("MINIAGENT_FEISHU_WS_AUTO_RECONNECT", "1")
    monkeypatch.setenv("MINIAGENT_FEISHU_WS_WATCHDOG_INTERVAL_S", "0.05")
    monkeypatch.setenv("MINIAGENT_FEISHU_WS_RECONNECT_GRACE_S", "0.15")
    monkeypatch.setenv("MINIAGENT_FEISHU_WS_DEAD_CONN_GRACE_S", "0.05")
    monkeypatch.setenv("MINIAGENT_FEISHU_WS_REFRESH_INTERVAL_S", "0")

    async def never_end():
        await asyncio.Event().wait()

    receive_task = asyncio.create_task(never_end())
    client = _mock_ws_client(connected=False, receive_task=receive_task)
    shutdown = asyncio.Event()

    reason = await asyncio.wait_for(
        supervise_feishu_ws_session(client, shutdown_event=shutdown),
        timeout=3.0,
    )
    receive_task.cancel()
    try:
        await receive_task
    except asyncio.CancelledError:
        pass

    assert reason == "watchdog_reconnect_grace"


@pytest.mark.asyncio
async def test_watchdog_dead_conn(monkeypatch):
    monkeypatch.setenv("MINIAGENT_FEISHU_WS_AUTO_RECONNECT", "0")
    monkeypatch.setenv("MINIAGENT_FEISHU_WS_WATCHDOG_INTERVAL_S", "0.05")
    monkeypatch.setenv("MINIAGENT_FEISHU_WS_DEAD_CONN_GRACE_S", "0.1")
    monkeypatch.setenv("MINIAGENT_FEISHU_WS_REFRESH_INTERVAL_S", "0")

    async def never_end():
        await asyncio.Event().wait()

    receive_task = asyncio.create_task(never_end())
    client = _mock_ws_client(connected=False, receive_task=receive_task)
    shutdown = asyncio.Event()

    reason = await asyncio.wait_for(
        supervise_feishu_ws_session(client, shutdown_event=shutdown),
        timeout=3.0,
    )
    receive_task.cancel()
    try:
        await receive_task
    except asyncio.CancelledError:
        pass

    assert reason == "watchdog_dead_conn"


@pytest.mark.asyncio
async def test_watchdog_refresh_interval(monkeypatch):
    monkeypatch.setenv("MINIAGENT_FEISHU_WS_WATCHDOG_INTERVAL_S", "0.05")
    monkeypatch.setenv("MINIAGENT_FEISHU_WS_REFRESH_INTERVAL_S", "0.15")
    monkeypatch.setenv("MINIAGENT_FEISHU_WS_DEAD_CONN_GRACE_S", "999")

    async def never_end():
        await asyncio.Event().wait()

    receive_task = asyncio.create_task(never_end())
    client = _mock_ws_client(connected=True, receive_task=receive_task)
    shutdown = asyncio.Event()

    reason = await asyncio.wait_for(
        supervise_feishu_ws_session(client, shutdown_event=shutdown),
        timeout=3.0,
    )
    receive_task.cancel()
    try:
        await receive_task
    except asyncio.CancelledError:
        pass

    assert reason == "watchdog_refresh"


@pytest.mark.asyncio
async def test_watchdog_idle_refresh(monkeypatch):
    monkeypatch.setenv("MINIAGENT_FEISHU_WS_WATCHDOG_INTERVAL_S", "0.05")
    monkeypatch.setenv("MINIAGENT_FEISHU_WS_IDLE_REFRESH_S", "0.1")
    monkeypatch.setenv("MINIAGENT_FEISHU_WS_REFRESH_INTERVAL_S", "0")
    monkeypatch.setenv("MINIAGENT_FEISHU_WS_DEAD_CONN_GRACE_S", "999")

    touch_ws_inbound_activity()
    # 模拟入站发生在很久以前
    ws_health._ws_last_inbound_monotonic = __import__("time").monotonic() - 200.0

    async def never_end():
        await asyncio.Event().wait()

    receive_task = asyncio.create_task(never_end())
    client = _mock_ws_client(connected=True, receive_task=receive_task)
    shutdown = asyncio.Event()

    reason = await asyncio.wait_for(
        supervise_feishu_ws_session(client, shutdown_event=shutdown),
        timeout=3.0,
    )
    receive_task.cancel()
    try:
        await receive_task
    except asyncio.CancelledError:
        pass

    assert reason == "watchdog_idle_refresh"


@pytest.mark.asyncio
async def test_request_feishu_ws_shutdown_sets_reason(monkeypatch):
    from miniagent.feishu import poll_server as ps

    monkeypatch.setenv("MINIAGENT_FEISHU_WS_WATCHDOG_INTERVAL_S", "3600")
    monkeypatch.setenv("MINIAGENT_FEISHU_WS_REFRESH_INTERVAL_S", "0")

    async def never_end():
        await asyncio.Event().wait()

    receive_task = asyncio.create_task(never_end())
    client = _mock_ws_client(connected=True, receive_task=receive_task)
    shutdown = asyncio.Event()
    ps._ws_shutdown_event = shutdown

    async def run_supervise():
        return await supervise_feishu_ws_session(client, shutdown_event=shutdown)

    supervise_task = asyncio.create_task(run_supervise())
    await asyncio.sleep(0.05)
    ps.request_feishu_ws_shutdown()
    reason = await asyncio.wait_for(supervise_task, timeout=3.0)
    receive_task.cancel()
    try:
        await receive_task
    except asyncio.CancelledError:
        pass
    ps._ws_shutdown_event = None

    assert reason == "shutdown"


@pytest.mark.asyncio
async def test_supervise_shutdown_event():
    async def never_end():
        await asyncio.Event().wait()

    receive_task = asyncio.create_task(never_end())
    client = _mock_ws_client(connected=True, receive_task=receive_task)
    shutdown = asyncio.Event()
    shutdown.set()

    reason = await supervise_feishu_ws_session(client, shutdown_event=shutdown)
    receive_task.cancel()
    try:
        await receive_task
    except asyncio.CancelledError:
        pass

    assert reason == "shutdown"


def test_read_feishu_ws_health_config_defaults(monkeypatch):
    monkeypatch.delenv("MINIAGENT_FEISHU_WS_WATCHDOG_INTERVAL_S", raising=False)
    cfg = read_feishu_ws_health_config()
    assert cfg.watchdog_interval_s == 30.0
    assert cfg.dead_conn_grace_s == 90.0
    assert cfg.refresh_interval_s == 0.0


def test_feishu_ws_auto_reconnect_default(monkeypatch):
    from miniagent.feishu.ws_client import feishu_ws_auto_reconnect_enabled

    monkeypatch.delenv("MINIAGENT_FEISHU_WS_AUTO_RECONNECT", raising=False)
    assert feishu_ws_auto_reconnect_enabled() is False
    monkeypatch.setenv("MINIAGENT_FEISHU_WS_AUTO_RECONNECT", "1")
    assert feishu_ws_auto_reconnect_enabled() is True


@pytest.mark.asyncio
async def test_reconnect_loop_holds_lock_after_supervised_return(monkeypatch, tmp_path):
    """会话监督正常 return 后外层重连，入站锁仍持有直至 stop。"""
    monkeypatch.setenv("MINI_AGENT_STATE", str(tmp_path))
    monkeypatch.setenv("FEISHU_APP_ID", "app_z")
    monkeypatch.setenv("FEISHU_APP_SECRET", "sec")
    monkeypatch.setenv("FEISHU_VERIFICATION_TOKEN", "tok")

    releases: list[str] = []

    def track_release(*args, **kwargs):
        releases.append("release")

    monkeypatch.setattr(
        "miniagent.infrastructure.feishu_inbound_lock.release_feishu_inbound_owner",
        track_release,
    )

    calls = {"n": 0}
    second_started = asyncio.Event()

    async def reset_noop():
        return None

    async def fake_start(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        second_started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(
        "miniagent.feishu.poll_server.reset_feishu_ws_singleton",
        reset_noop,
    )
    monkeypatch.setattr(
        "miniagent.feishu.poll_server.start_feishu_poll_server",
        fake_start,
    )

    async def instant_sleep(_t: float):
        return

    monkeypatch.setattr("asyncio.sleep", instant_sleep)

    from miniagent.engine.feishu_state import FeishuRuntime
    from miniagent.infrastructure.message_queue import MessageQueueManager

    mq = MessageQueueManager()
    rt = FeishuRuntime(mq)

    def factory(_tb, _tp, _st):
        async def h(_c, _cid, _sid, _ct="group"):
            return ""

        return h

    rt.start([], [], factory, {"instance_id": 7})

    try:
        await asyncio.wait_for(second_started.wait(), timeout=3.0)
        assert not releases, "监督结束后外层重连期间不应释放入站锁"
    finally:
        t = rt.get_task()
        if t and not t.done():
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
        await rt.stop_async()
