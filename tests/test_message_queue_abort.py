"""MessageQueueManager.abort_chat 与 dispatch_command 中止队列。"""

from __future__ import annotations

import asyncio

import pytest


@pytest.mark.asyncio
async def test_abort_chat_cancels_running_and_pending_in_queue_mode():
    from miniagent.infrastructure.message_queue import MessageQueueManager, QueueMode

    mq = MessageQueueManager()
    assert mq.mode == QueueMode.QUEUE
    events: list[str] = []

    async def work(tag: str, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
            events.append(f"{tag}_done")
        except asyncio.CancelledError:
            events.append(f"{tag}_cancelled")
            raise

    asyncio.create_task(mq.dispatch("room1", work("a", 100.0)))
    await asyncio.sleep(0.02)
    asyncio.create_task(mq.dispatch("room1", work("b", 100.0)))
    await asyncio.sleep(0.02)

    r = mq.abort_chat("room1")
    assert r["chat_id"] == "room1"
    assert r.get("cancelled_preemptive_current") is False
    assert r.get("cancelled_dispatch_wait") == 0
    assert r["cancelled_running"] is True
    assert r["cancelled_pending"] >= 1

    await asyncio.sleep(0)
    assert "a_cancelled" in events or "b_cancelled" in events

    asyncio.create_task(mq.dispatch("room1", work("c", 0.01)))
    await asyncio.sleep(0.05)
    assert "c_done" in events


@pytest.mark.asyncio
async def test_abort_chat_idle_chat():
    from miniagent.infrastructure.message_queue import MessageQueueManager

    mq = MessageQueueManager()
    r = mq.abort_chat("never_used")
    assert r["cancelled_running"] is False
    assert r["cancelled_pending"] == 0
    assert r.get("cancelled_dispatch_wait") == 0


@pytest.mark.asyncio
async def test_dispatch_abort_respects_message_queue_abort_chat_id():
    from miniagent.engine.command_dispatch import dispatch_command
    from miniagent.engine.engine import UnifiedEngine
    from miniagent.engine.feishu_state import FeishuRuntime
    from miniagent.infrastructure.channel_router import ChannelRouter
    from miniagent.infrastructure.message_queue import MessageQueueManager
    from miniagent.infrastructure.monitor import DefaultToolMonitor
    from miniagent.infrastructure.registry import DefaultToolRegistry
    from miniagent.runtime.context import RuntimeContext
    from miniagent.skills import DefaultSkillRegistry, create_clawhub_client
    from tests.test_startup import _make_memory_bundle

    mq = MessageQueueManager()
    seen: list[str] = []
    real_abort = mq.abort_chat

    def wrapped_abort(cid: str):
        seen.append(cid)
        return real_abort(cid)

    mq.abort_chat = wrapped_abort  # type: ignore[method-assign]

    ms, al, ki = _make_memory_bundle()
    ctx = RuntimeContext(
        registry=DefaultToolRegistry(),
        monitor=DefaultToolMonitor(),
        skill_registry=DefaultSkillRegistry(),
        clawhub=create_clawhub_client(),
        engine=UnifiedEngine(),
        channel_router=ChannelRouter(),
        message_queue=mq,
        feishu=FeishuRuntime(mq),
        memory_store=ms,
        activity_log=al,
        keyword_index=ki,
    )
    state = {
        "active_session_id": "default",
        "skill_toolboxes": [],
        "skill_prompts": [],
        "feishu_enabled": True,
        "session_manager": None,
        "instance_id": 1,
        "runtime_ctx": ctx,
        "feishu_p2p_synced_senders": set(),
    }

    out = await dispatch_command(
        ".abort",
        state=state,
        capture=True,
        message_queue_abort_chat_id="oc_feishu_room",
    )
    assert seen == ["oc_feishu_room"]
    assert out is not None
    assert "队列" in out or "进程" in out

    seen.clear()
    out2 = await dispatch_command(".queue abort", state=state, capture=True)
    assert seen == [mq.CLI_CHAT_ID]
    assert out2 is not None


@pytest.mark.asyncio
async def test_abort_chat_cancels_dispatch_wait_task():
    from miniagent.infrastructure.message_queue import MessageQueueManager, QueueMode

    mq = MessageQueueManager()
    assert mq.mode == QueueMode.QUEUE
    events: list[str] = []

    async def long_job() -> None:
        try:
            await asyncio.sleep(100.0)
        except asyncio.CancelledError:
            events.append("wait_cancelled")
            raise

    async def runner() -> None:
        await mq.dispatch_wait("dw1", long_job())

    asyncio.create_task(runner())
    await asyncio.sleep(0.05)
    r = mq.abort_chat("dw1")
    assert r["chat_id"] == "dw1"
    assert int(r.get("cancelled_dispatch_wait") or 0) >= 1
    await asyncio.sleep(0.05)
    assert "wait_cancelled" in events


@pytest.mark.asyncio
async def test_abort_chat_preemptive_cancels_current():
    from miniagent.infrastructure.message_queue import MessageQueueManager, QueueMode

    mq = MessageQueueManager()
    mq.mode = QueueMode.PREEMPTIVE

    async def hold() -> None:
        await asyncio.sleep(100.0)

    asyncio.create_task(mq.dispatch("p1", hold()))
    await asyncio.sleep(0.05)
    r = mq.abort_chat("p1")
    assert r["cancelled_preemptive_current"] is True
    assert r.get("cancelled_running") is True
    assert int(r.get("cancelled_dispatch_wait") or 0) == 0
