"""飞书 WebSocket 会话监督与看门狗单测。"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from miniagent.assistant.feishu.ws_health import (
    FeishuWsHealthState,
    _receive_loop_exit_reason,
    read_feishu_ws_health_config,
    supervise_feishu_ws_session,
)
from tests.support.config import install_test_config


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
    health = FeishuWsHealthState()
    reason = await supervise_feishu_ws_session(client, shutdown_event=shutdown, health_state=health)
    assert reason.startswith("receive_loop_exit")
    end_reason, _ = health.last_session_end()
    assert end_reason == reason


@pytest.mark.asyncio
async def test_receive_loop_ping_timeout_has_stable_recoverable_reason():
    class PingTimeout(Exception):
        code = 3003

    async def fail():
        raise PingTimeout("received 3003 ping_timeout")

    task = asyncio.create_task(fail())
    with pytest.raises(PingTimeout):
        await task

    assert _receive_loop_exit_reason(task) == "receive_loop_ping_timeout"


def test_health_state_tracks_physical_session_duration(monkeypatch):
    ticks = iter((100.0, 175.5))
    monkeypatch.setattr("miniagent.assistant.feishu.ws_health.time.monotonic", lambda: next(ticks))
    health = FeishuWsHealthState()
    health.record_session_start()
    health.record_session_end("receive_loop_ping_timeout")
    assert health.last_session_duration_s == 75.5
    assert health.session_started_monotonic is None


@pytest.mark.asyncio
async def test_watchdog_reconnect_grace_sdk_auto(tmp_path):
    install_test_config(
        tmp_path,
        {
            "feishu": {
                "websocket": {
                    "auto_reconnect": True,
                    "watchdog_interval": 0.05,
                    "reconnect_grace": 0.15,
                    "dead_conn_grace": 0.05,
                    "refresh_interval": 0,
                }
            }
        },
    )

    async def never_end():
        await asyncio.Event().wait()

    receive_task = asyncio.create_task(never_end())
    client = _mock_ws_client(connected=False, receive_task=receive_task)
    shutdown = asyncio.Event()

    reason = await asyncio.wait_for(
        supervise_feishu_ws_session(
            client, shutdown_event=shutdown, health_state=FeishuWsHealthState()
        ),
        timeout=3.0,
    )
    receive_task.cancel()
    try:
        await receive_task
    except asyncio.CancelledError:
        pass

    assert reason == "watchdog_reconnect_grace"


@pytest.mark.asyncio
async def test_watchdog_dead_conn(tmp_path):
    install_test_config(
        tmp_path,
        {
            "feishu": {
                "websocket": {
                    "auto_reconnect": False,
                    "watchdog_interval": 0.05,
                    "dead_conn_grace": 0.1,
                    "refresh_interval": 0,
                }
            }
        },
    )

    async def never_end():
        await asyncio.Event().wait()

    receive_task = asyncio.create_task(never_end())
    client = _mock_ws_client(connected=False, receive_task=receive_task)
    shutdown = asyncio.Event()

    reason = await asyncio.wait_for(
        supervise_feishu_ws_session(
            client, shutdown_event=shutdown, health_state=FeishuWsHealthState()
        ),
        timeout=3.0,
    )
    receive_task.cancel()
    try:
        await receive_task
    except asyncio.CancelledError:
        pass

    assert reason == "watchdog_dead_conn"


@pytest.mark.asyncio
async def test_watchdog_refresh_interval(tmp_path):
    install_test_config(
        tmp_path,
        {
            "feishu": {
                "websocket": {
                    "watchdog_interval": 0.05,
                    "refresh_interval": 0.15,
                    "dead_conn_grace": 999,
                }
            }
        },
    )

    async def never_end():
        await asyncio.Event().wait()

    receive_task = asyncio.create_task(never_end())
    client = _mock_ws_client(connected=True, receive_task=receive_task)
    shutdown = asyncio.Event()

    reason = await asyncio.wait_for(
        supervise_feishu_ws_session(
            client, shutdown_event=shutdown, health_state=FeishuWsHealthState()
        ),
        timeout=3.0,
    )
    receive_task.cancel()
    try:
        await receive_task
    except asyncio.CancelledError:
        pass

    assert reason == "watchdog_refresh"


@pytest.mark.asyncio
async def test_watchdog_idle_refresh(tmp_path):
    install_test_config(
        tmp_path,
        {
            "feishu": {
                "websocket": {
                    "watchdog_interval": 0.05,
                    "idle_refresh": 0.1,
                    "refresh_interval": 0,
                    "dead_conn_grace": 999,
                }
            }
        },
    )

    health = FeishuWsHealthState()
    health.touch_inbound()
    # 模拟入站发生在很久以前
    health.last_inbound_monotonic = __import__("time").monotonic() - 200.0

    async def never_end():
        await asyncio.Event().wait()

    receive_task = asyncio.create_task(never_end())
    client = _mock_ws_client(connected=True, receive_task=receive_task)
    shutdown = asyncio.Event()

    reason = await asyncio.wait_for(
        supervise_feishu_ws_session(client, shutdown_event=shutdown, health_state=health),
        timeout=3.0,
    )
    receive_task.cancel()
    try:
        await receive_task
    except asyncio.CancelledError:
        pass

    assert reason == "watchdog_idle_refresh"


@pytest.mark.asyncio
async def test_poll_state_shutdown_sets_supervisor_reason(tmp_path):
    from miniagent.assistant.feishu import poll_server as ps

    install_test_config(
        tmp_path,
        {
            "feishu": {
                "websocket": {
                    "watchdog_interval": 3600,
                    "refresh_interval": 0,
                }
            }
        },
    )

    async def never_end():
        await asyncio.Event().wait()

    receive_task = asyncio.create_task(never_end())
    client = _mock_ws_client(connected=True, receive_task=receive_task)
    shutdown = asyncio.Event()
    state = ps.FeishuPollState()
    state.shutdown_event = shutdown

    async def run_supervise():
        return await supervise_feishu_ws_session(
            client,
            shutdown_event=shutdown,
            health_state=state.ws_health,
        )

    supervise_task = asyncio.create_task(run_supervise())
    await asyncio.sleep(0.05)
    state.request_shutdown()
    reason = await asyncio.wait_for(supervise_task, timeout=3.0)
    receive_task.cancel()
    try:
        await receive_task
    except asyncio.CancelledError:
        pass

    assert reason == "shutdown"


@pytest.mark.asyncio
async def test_supervise_shutdown_event():
    async def never_end():
        await asyncio.Event().wait()

    receive_task = asyncio.create_task(never_end())
    client = _mock_ws_client(connected=True, receive_task=receive_task)
    shutdown = asyncio.Event()
    shutdown.set()

    reason = await supervise_feishu_ws_session(
        client,
        shutdown_event=shutdown,
        health_state=FeishuWsHealthState(),
    )
    receive_task.cancel()
    try:
        await receive_task
    except asyncio.CancelledError:
        pass

    assert reason == "shutdown"


@pytest.mark.asyncio
async def test_supervisor_cancellation_cleans_receive_task_and_disconnects():
    async def never_end():
        await asyncio.Event().wait()

    receive_task = asyncio.create_task(never_end())
    client = _mock_ws_client(connected=True, receive_task=receive_task)
    health = FeishuWsHealthState()
    supervisor = asyncio.create_task(
        supervise_feishu_ws_session(
            client,
            shutdown_event=asyncio.Event(),
            health_state=health,
        )
    )
    await asyncio.sleep(0)

    supervisor.cancel()
    with pytest.raises(asyncio.CancelledError):
        await supervisor

    assert receive_task.done()
    assert receive_task.cancelled()
    client._disconnect.assert_awaited_once()
    assert health.last_session_end()[0] == "cancelled"


@pytest.mark.asyncio
async def test_receive_cleanup_error_does_not_break_shutdown_result():
    async def fail_during_cancel():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError as error:
            raise RuntimeError("receive cleanup failed") from error

    receive_task = asyncio.create_task(fail_during_cancel())
    client = _mock_ws_client(connected=True, receive_task=receive_task)
    shutdown = asyncio.Event()
    supervisor = asyncio.create_task(
        supervise_feishu_ws_session(
            client,
            shutdown_event=shutdown,
            health_state=FeishuWsHealthState(),
        )
    )
    await asyncio.sleep(0)
    shutdown.set()

    assert await supervisor == "shutdown"
    assert receive_task.done()
    assert isinstance(receive_task.exception(), RuntimeError)
    client._disconnect.assert_awaited_once()


def test_read_feishu_ws_health_config_defaults(tmp_path):
    install_test_config(tmp_path)
    cfg = read_feishu_ws_health_config()
    assert cfg.watchdog_interval_s == 30.0
    assert cfg.dead_conn_grace_s == 90.0
    assert cfg.refresh_interval_s == 0.0


def test_stable_session_resets_reconnect_backoff():
    from miniagent.assistant.engine.feishu_state import _next_reconnect_attempt

    assert (
        _next_reconnect_attempt(
            6,
            session_duration_s=300.0,
            stable_reset_after_s=60.0,
        )
        == 1
    )
    assert (
        _next_reconnect_attempt(
            2,
            session_duration_s=5.0,
            stable_reset_after_s=60.0,
        )
        == 3
    )


def test_feishu_ws_auto_reconnect_default(tmp_path):
    from miniagent.assistant.feishu.ws_client import feishu_ws_auto_reconnect_enabled

    install_test_config(tmp_path)
    assert feishu_ws_auto_reconnect_enabled() is False
    install_test_config(tmp_path, {"feishu": {"websocket": {"auto_reconnect": True}}})
    assert feishu_ws_auto_reconnect_enabled() is True


@pytest.mark.asyncio
async def test_reconnect_loop_holds_lock_after_supervised_return(monkeypatch, tmp_path):
    """会话监督正常 return 后外层重连，入站锁仍持有直至 stop。"""
    install_test_config(tmp_path, {"paths": {"state_dir": str(tmp_path)}})
    monkeypatch.setenv("FEISHU_APP_ID", "app_z")
    monkeypatch.setenv("FEISHU_APP_SECRET", "sec")
    monkeypatch.setenv("FEISHU_VERIFICATION_TOKEN", "tok")

    releases: list[str] = []

    def track_release(*args, **kwargs):
        releases.append("release")

    monkeypatch.setattr(
        "miniagent.assistant.infrastructure.feishu_inbound_lock.release_feishu_inbound_owner",
        track_release,
    )

    calls = {"n": 0}
    second_started = asyncio.Event()

    async def fake_start(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        second_started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(
        "miniagent.assistant.feishu.poll_server.start_feishu_poll_server",
        fake_start,
    )

    async def instant_sleep(_t: float):
        return

    monkeypatch.setattr("asyncio.sleep", instant_sleep)

    from miniagent.assistant.engine.feishu_state import FeishuRuntime
    from miniagent.assistant.infrastructure.message_queue import MessageQueueManager

    mq = MessageQueueManager()
    rt = FeishuRuntime(mq)

    def factory(_st):
        async def h(_c, _cid, _sid, _ct="group"):
            return ""

        return h

    rt.start(factory, {"instance_id": 7})

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
