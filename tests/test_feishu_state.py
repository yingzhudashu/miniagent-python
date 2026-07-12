"""FeishuRuntime：status 与 stop 入站锁行为。"""

from __future__ import annotations

import asyncio

import pytest

from miniagent.engine.feishu_state import FeishuRuntime
from miniagent.infrastructure.message_queue import MessageQueueManager


def _lines_rt() -> tuple[FeishuRuntime, list[str]]:
    mq = MessageQueueManager()
    rt = FeishuRuntime(mq)
    lines: list[str] = []
    rt._user_status = lines.append  # noqa: SLF001
    return rt, lines


@pytest.mark.asyncio
async def test_runtime_done_callback_consumes_failure_and_clears_state() -> None:
    rt, _lines = _lines_rt()

    async def fail() -> None:
        raise RuntimeError("ws failed")

    task = asyncio.create_task(fail())
    rt.set_task(task)
    rt.set_running(True)
    task.add_done_callback(rt._on_runtime_task_done)
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert rt.get_task() is None
    assert rt.is_running() is False


def test_status_when_not_running() -> None:
    rt, lines = _lines_rt()
    rt.set_running(False)
    rt.status()
    assert any("\u26aa \u98de\u4e66: \u672a\u542f\u7528" in s for s in lines)


def test_status_when_running() -> None:
    rt, lines = _lines_rt()
    rt.set_running(True)
    rt.status()
    assert any("\U0001f7e2 \u98de\u4e66: \u8fd0\u884c\u4e2d" in s for s in lines)


def test_status_includes_ws_health_and_lock(monkeypatch) -> None:
    rt, lines = _lines_rt()
    rt.set_running(True)

    poll_state = rt._ensure_poll_state()  # noqa: SLF001
    poll_state.ws_health.last_session_end_reason = "disconnect"
    poll_state.ws_health.last_session_end_at = 1_700_000_000.0
    poll_state.ws_health.last_inbound_monotonic = 100.0
    monkeypatch.setattr("time.monotonic", lambda: 130.0)
    monkeypatch.setattr(
        "miniagent.infrastructure.feishu_inbound_lock.read_feishu_inbound_owner",
        lambda *a, **k: {"pid": 1234, "instance_id": 7, "alive": True},
    )

    rt.status()
    text = "\n".join(lines)
    assert "disconnect" in text
    assert "PID=1234" in text
    assert "\u5b9e\u4f8b#7" in text
    assert "\u6700\u540e\u5165\u7ad9\u7ea6 30s \u524d" in text


@pytest.mark.asyncio
async def test_stop_sync_defers_inbound_lock_release_until_task_finally(
    monkeypatch, tmp_path
) -> None:
    """同步 stop() 不在 cancel 后立即释放入站锁，由 task finally 释放。"""
    monkeypatch.setenv("MINIAGENT_PATHS_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("FEISHU_APP_ID", "app_z")
    monkeypatch.setenv("FEISHU_APP_SECRET", "sec")
    monkeypatch.setenv("FEISHU_VERIFICATION_TOKEN", "tok")

    releases: list[str] = []

    def track_release(*_a, **_k):
        releases.append("release")

    monkeypatch.setattr(
        "miniagent.infrastructure.feishu_inbound_lock.release_feishu_inbound_owner",
        track_release,
    )

    entered = asyncio.Event()
    block = asyncio.Event()

    async def fake_poll(*_a, **_k):
        entered.set()
        await block.wait()

    monkeypatch.setattr("miniagent.feishu.poll_server.start_feishu_poll_server", fake_poll)

    mq = MessageQueueManager()
    rt = FeishuRuntime(mq)

    def factory(_st):
        async def h(_c, _cid, _sid, _ct="group"):
            return ""

        return h

    rt.start(factory, {"runtime_ctx": None, "instance_id": 3})
    t = rt.get_task()
    assert t is not None
    await asyncio.wait_for(entered.wait(), timeout=3.0)

    rt.stop()
    assert not releases, "cancel 后、task finally 前不应释放入站锁"

    if t and not t.done():
        await asyncio.gather(t, return_exceptions=True)

    assert releases, "task 退出后应释放入站锁"
