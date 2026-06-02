"""飞书常驻重连：入站锁仅在任务结束时释放，不在单次连接失败时释放。"""

from __future__ import annotations

import asyncio

import pytest


@pytest.mark.asyncio
async def test_reconnect_loop_holds_inbound_lock_until_stop(monkeypatch, tmp_path):
    monkeypatch.setenv("MINIAGENT_PATHS_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("FEISHU_APP_ID", "app_x")
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
    first_fail = asyncio.Event()

    async def reset_noop():
        return None

    async def fake_start_feishu_poll_server(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            first_fail.set()
            raise OSError(121, "信号灯超时时间已到")
        await asyncio.Event().wait()

    monkeypatch.setattr(
        "miniagent.feishu.poll_server.reset_feishu_ws_singleton",
        reset_noop,
    )
    monkeypatch.setattr(
        "miniagent.feishu.poll_server.start_feishu_poll_server",
        fake_start_feishu_poll_server,
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

    rt.start([], [], factory, {"runtime_ctx": None, "instance_id": 42})

    try:
        await asyncio.wait_for(first_fail.wait(), timeout=3.0)
        await asyncio.sleep(0)
        assert not releases, "单次连接失败后不应释放入站锁"
    finally:
        t = rt.get_task()
        if t and not t.done():
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
        rt.stop()

    assert len(releases) >= 1, "停止或任务退出后应释放入站锁"


@pytest.mark.asyncio
async def test_first_connection_failure_skips_backoff_sleep(monkeypatch, tmp_path):
    """首次 await start 前不 sleep；失败后第二次迭代才执行退避 sleep。"""
    monkeypatch.setenv("MINIAGENT_PATHS_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("FEISHU_APP_ID", "app_y")
    monkeypatch.setenv("FEISHU_APP_SECRET", "sec")
    monkeypatch.setenv("FEISHU_VERIFICATION_TOKEN", "tok")

    monkeypatch.setattr(
        "miniagent.infrastructure.feishu_inbound_lock.release_feishu_inbound_owner",
        lambda *a, **k: None,
    )

    sleeps: list[float] = []

    async def track_sleep(t: float):
        sleeps.append(t)

    monkeypatch.setattr("asyncio.sleep", track_sleep)

    calls = {"n": 0}
    second_entered = asyncio.Event()
    hold = asyncio.Event()

    async def reset_noop():
        return None

    async def fake_start(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError(121, "x")
        second_entered.set()
        await hold.wait()

    monkeypatch.setattr("miniagent.feishu.poll_server.reset_feishu_ws_singleton", reset_noop)
    monkeypatch.setattr("miniagent.feishu.poll_server.start_feishu_poll_server", fake_start)

    from miniagent.engine.feishu_state import FeishuRuntime
    from miniagent.infrastructure.message_queue import MessageQueueManager

    mq = MessageQueueManager()
    rt = FeishuRuntime(mq)

    def factory(_tb, _tp, _st):
        async def h(_c, _cid, _sid, _ct="group"):
            return ""

        return h

    rt.start([], [], factory, {"runtime_ctx": None, "instance_id": 1})
    t = rt.get_task()
    assert t is not None
    try:
        await asyncio.wait_for(second_entered.wait(), timeout=3.0)
        assert sleeps, "第二次进入 start 前应已执行退避 sleep"
        assert calls["n"] >= 2
    finally:
        hold.set()
        if t and not t.done():
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
        rt.stop()
