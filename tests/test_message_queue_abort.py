"""MessageQueueManager.abort_chat 与 dispatch_command 中止队列。"""

from __future__ import annotations

import asyncio

import pytest

from tests.memory_helpers import (
    make_background_task_manager,
    make_knowledge_registry,
    make_memory_runtime,
)


@pytest.mark.asyncio
async def test_abort_chat_cancels_running_and_pending_in_queue_mode():
    from miniagent.assistant.infrastructure.message_queue import MessageQueueManager, QueueMode

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
    from miniagent.assistant.infrastructure.message_queue import MessageQueueManager

    mq = MessageQueueManager()
    r = mq.abort_chat("never_used")
    assert r["cancelled_running"] is False
    assert r["cancelled_pending"] == 0
    assert r.get("cancelled_dispatch_wait") == 0


@pytest.mark.asyncio
async def test_dispatch_abort_respects_message_queue_abort_chat_id():
    from miniagent.agent.monitor import DefaultToolMonitor
    from miniagent.assistant.bootstrap.application import ApplicationContainer
    from miniagent.assistant.engine.command_dispatch import dispatch_command
    from miniagent.assistant.engine.engine import UnifiedEngine
    from miniagent.assistant.engine.feishu_state import FeishuRuntime
    from miniagent.assistant.infrastructure.channel_router import ChannelRouter
    from miniagent.assistant.infrastructure.message_queue import MessageQueueManager
    from miniagent.assistant.infrastructure.registry import DefaultToolRegistry
    from miniagent.assistant.skills import DefaultSkillRegistry, create_clawhub_client
    from tests.test_startup import _make_memory_bundle

    mq = MessageQueueManager()
    seen: list[str] = []
    real_abort = mq.abort_chat

    def wrapped_abort(cid: str):
        seen.append(cid)
        return real_abort(cid)

    mq.abort_chat = wrapped_abort  # type: ignore[method-assign]

    ms, al, ki, mc = _make_memory_bundle()
    ctx = ApplicationContainer(
        registry=DefaultToolRegistry(),
        monitor=DefaultToolMonitor(),
        skill_registry=DefaultSkillRegistry(),
        clawhub=create_clawhub_client(),
        engine=UnifiedEngine(),
        channel_router=ChannelRouter(),
        message_queue=mq,
        feishu=FeishuRuntime(mq),
        memory=make_memory_runtime(store=ms, activity_log=al, keyword_index=ki, context=mc),
        knowledge_registry=make_knowledge_registry(),
        background_tasks=make_background_task_manager(),
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
        "/abort",
        state=state,
        capture=True,
        message_queue_abort_chat_id="oc_feishu_room",
    )
    assert seen == ["oc_feishu_room"]
    assert out is not None
    assert "队列" in out or "进程" in out

    seen.clear()
    out2 = await dispatch_command("/queue abort", state=state, capture=True)
    assert seen == [mq.CLI_CHAT_ID]
    assert out2 is not None


@pytest.mark.asyncio
async def test_abort_chat_cancels_dispatch_wait_task():
    from miniagent.assistant.infrastructure.message_queue import MessageQueueManager, QueueMode

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
    from miniagent.assistant.infrastructure.message_queue import MessageQueueManager, QueueMode

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


@pytest.mark.asyncio
async def test_completed_queue_tasks_do_not_consume_queue_capacity() -> None:
    from miniagent.assistant.infrastructure.message_queue import MessageQueueManager

    mq = MessageQueueManager()
    finished = asyncio.Event()

    async def quick_job() -> None:
        finished.set()

    await mq.dispatch("room-cleanup", quick_job())
    await asyncio.wait_for(finished.wait(), timeout=1.0)
    await asyncio.sleep(0)

    assert mq.get_agent_status("room-cleanup")["pending"] == 0


@pytest.mark.asyncio
async def test_switch_to_preemptive_cancels_existing_queue_wrappers() -> None:
    from miniagent.assistant.infrastructure.message_queue import MessageQueueManager, QueueMode

    mq = MessageQueueManager()
    cancelled: list[str] = []

    async def hold(name: str) -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(name)
            raise

    await mq.dispatch("room-switch", hold("first"))
    await mq.dispatch("room-switch", hold("second"))
    await asyncio.sleep(0)

    mq.mode = QueueMode.PREEMPTIVE
    await mq.dispatch("room-switch", asyncio.sleep(0))
    await asyncio.sleep(0)

    assert cancelled == ["first"]
    assert mq.get_agent_status("room-switch")["pending"] == 0


@pytest.mark.asyncio
async def test_shutdown_awaits_queue_task_cancellation() -> None:
    from miniagent.assistant.infrastructure.message_queue import MessageQueueManager

    mq = MessageQueueManager()
    cancelled = asyncio.Event()

    async def hold() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await asyncio.sleep(0)
            cancelled.set()
            raise

    await mq.dispatch("room-shutdown", hold())
    await asyncio.sleep(0)
    await mq.shutdown()

    assert cancelled.is_set()
    assert mq.get_agent_status("room-shutdown") == {
        "busy": False,
        "pending": 0,
        "elapsed_seconds": None,
        "status": "idle",
    }

    with pytest.raises(RuntimeError, match="已关闭"):
        await mq.dispatch("room-shutdown", asyncio.sleep(0))


@pytest.mark.asyncio
async def test_transient_chat_queues_are_reclaimed_and_status_is_read_only() -> None:
    from miniagent.assistant.infrastructure.message_queue import MessageQueueManager

    mq = MessageQueueManager()

    async def completed() -> None:
        await asyncio.sleep(0)

    for index in range(250):
        await mq.dispatch_wait(f"transient-{index}", completed())

    assert mq._queues == {}
    assert mq.get_agent_status("never-seen")["status"] == "idle"
    assert mq._queues == {}
