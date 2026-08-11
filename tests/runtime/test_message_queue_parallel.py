"""MessageQueueManager 并行模式测试。"""

from __future__ import annotations

import asyncio

import pytest

from miniagent.assistant.infrastructure.message_queue import MessageQueueManager


@pytest.mark.asyncio
async def test_cross_queue_parallel_when_disabled_serial() -> None:
    mq = MessageQueueManager()
    mq.cross_queue_serial = False

    order: list[str] = []
    in_flight = 0
    overlap = False

    async def work(chat_id: str) -> None:
        nonlocal in_flight, overlap
        order.append(f"start:{chat_id}")
        in_flight += 1
        if in_flight >= 2:
            overlap = True
        await asyncio.sleep(0.06)
        in_flight -= 1
        order.append(f"end:{chat_id}")

    await asyncio.gather(
        mq.dispatch("chat_a", work("chat_a")),
        mq.dispatch("chat_b", work("chat_b")),
    )
    assert overlap is True


@pytest.mark.asyncio
async def test_cross_queue_serial_global_fifo() -> None:
    mq = MessageQueueManager()
    mq.cross_queue_serial = True
    mq.ensure_exec_lock()

    order: list[str] = []

    async def work(chat_id: str) -> None:
        order.append(f"start:{chat_id}")
        await asyncio.sleep(0.02)
        order.append(f"end:{chat_id}")

    await asyncio.gather(
        mq.dispatch_wait("chat_a", work("chat_a")),
        mq.dispatch_wait("chat_b", work("chat_b")),
    )
    assert order == ["start:chat_a", "end:chat_a", "start:chat_b", "end:chat_b"]


@pytest.mark.asyncio
async def test_cancellation_while_waiting_for_global_lock_resets_queue_state() -> None:
    mq = MessageQueueManager()
    mq.cross_queue_serial = True
    mq.ensure_exec_lock()
    assert mq.exec_lock is not None
    await mq.exec_lock.acquire()

    task = asyncio.create_task(mq.dispatch_wait("chat", asyncio.sleep(60)))
    for _ in range(100):
        queue = mq._queues.get("chat")
        if queue is not None and queue.is_busy:
            break
        await asyncio.sleep(0)
    else:
        pytest.fail("queue did not begin waiting for the global lock")

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    mq.exec_lock.release()

    assert queue.is_busy is False
    assert queue.elapsed_seconds is None


@pytest.mark.asyncio
async def test_queue_execution_invokes_start_and_done_callbacks() -> None:
    mq = MessageQueueManager()
    callbacks: list[str] = []

    await mq.dispatch_wait(
        "chat",
        asyncio.sleep(0),
        on_start=lambda: callbacks.append("start"),
        on_done=lambda: callbacks.append("done"),
    )

    assert callbacks == ["start", "done"]
