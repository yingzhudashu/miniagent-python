"""ConfirmationChannel 单元测试 — 边界与并发行为。"""

from __future__ import annotations

import asyncio

import pytest

from miniagent.core.confirmation_channel import ConfirmationChannel
from miniagent.types.confirmation import ConfirmationRequest, ConfirmationResult, ConfirmationStage


def _req(content: str = "Q", stage: ConfirmationStage = ConfirmationStage.PLAN) -> ConfirmationRequest:
    return ConfirmationRequest(stage=stage, content=content)


@pytest.mark.asyncio
async def test_request_and_respond_roundtrip() -> None:
    ch = ConfirmationChannel()
    task = asyncio.create_task(ch.request_confirmation(_req("plan")))
    await asyncio.sleep(0.01)
    assert ch.has_pending
    ch.respond(ConfirmationResult.confirm())
    result = await task
    assert result.approved is True
    assert not ch.has_pending


@pytest.mark.asyncio
async def test_concurrent_request_while_waiting_raises() -> None:
    ch = ConfirmationChannel()
    waiter = asyncio.create_task(
        ch.request_confirmation(_req("first", ConfirmationStage.CLARIFICATION))
    )
    await asyncio.sleep(0.01)
    with pytest.raises(RuntimeError, match="已有确认请求正在处理"):
        await ch.request_confirmation(_req("second"))
    ch.respond(ConfirmationResult.clarification_reply("ok"))
    await waiter


@pytest.mark.asyncio
async def test_double_respond_ignores_second() -> None:
    ch = ConfirmationChannel()
    task = asyncio.create_task(ch.request_confirmation(_req("tool", ConfirmationStage.TOOL)))
    await asyncio.sleep(0.01)
    ch.respond(ConfirmationResult(approved=True, adjustment="first"))
    ch.respond(ConfirmationResult(approved=True, adjustment="second"))
    result = await task
    assert result.adjustment == "first"


@pytest.mark.asyncio
async def test_respond_without_pending_is_noop() -> None:
    ch = ConfirmationChannel()
    ch.respond(ConfirmationResult.confirm())
    assert not ch.has_pending


@pytest.mark.asyncio
async def test_has_pending_false_after_respond_before_cleanup() -> None:
    ch = ConfirmationChannel()
    task = asyncio.create_task(ch.request_confirmation(_req()))
    await asyncio.sleep(0.01)
    ch.respond(ConfirmationResult.confirm())
    assert not ch.has_pending
    assert ch.pending is None
    await task


@pytest.mark.asyncio
async def test_request_blocked_until_previous_slot_released() -> None:
    ch = ConfirmationChannel()
    first = asyncio.create_task(ch.request_confirmation(_req("one")))
    await asyncio.sleep(0.01)
    ch.respond(ConfirmationResult(approved=True, adjustment="ans1"))

    blocked = asyncio.create_task(ch.request_confirmation(_req("two")))
    await asyncio.sleep(0.01)
    assert not blocked.done()

    ans1 = await first
    assert ans1.adjustment == "ans1"

    await asyncio.sleep(0.01)
    assert ch.has_pending
    ch.respond(ConfirmationResult(approved=True, adjustment="ans2"))
    ans2 = await blocked
    assert ans2.adjustment == "ans2"


@pytest.mark.asyncio
async def test_result_none_after_event_set_raises() -> None:
    ch = ConfirmationChannel()
    task = asyncio.create_task(ch.request_confirmation(_req()))
    await asyncio.sleep(0.01)
    # 人为破坏：置 event 但不写 result
    with ch._lock:
        ch._event.set()
    with pytest.raises(RuntimeError, match="确认响应为 None"):
        await task


@pytest.mark.asyncio
async def test_respond_from_thread() -> None:
    ch = ConfirmationChannel()
    task = asyncio.create_task(
        ch.request_confirmation(_req("thread", ConfirmationStage.CLARIFICATION))
    )
    await asyncio.sleep(0.01)

    import threading

    threading.Thread(
        target=ch.respond,
        args=(ConfirmationResult.clarification_reply("from-thread"),),
    ).start()

    result = await asyncio.wait_for(task, timeout=2.0)
    assert result.adjustment == "from-thread"
