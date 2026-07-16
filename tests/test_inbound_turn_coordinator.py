"""Inbound coordinator, CLI mapping and queue behavior tests."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from miniagent.assistant.application.messaging.inbound import InboundTurnCoordinator
from miniagent.assistant.contracts.messages import InboundMessage
from miniagent.assistant.engine.cli_inbound import (
    CLI_CHANNEL,
    CLI_CONVERSATION_ID,
    CLI_SENDER_ID,
    build_cli_inbound_message,
)
from miniagent.assistant.infrastructure.message_queue import MessageQueueManager, QueueMode


class RecordingQueue:
    """Queue double that executes turns while recording the selected API and key."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def dispatch(
        self, chat_id: str, coro: Any, on_start: Any = None, on_done: Any = None
    ) -> None:
        """Record non-wait dispatch and run the supplied turn."""
        self.calls.append(("dispatch", chat_id))
        if on_start:
            on_start()
        await coro
        if on_done:
            on_done()

    async def dispatch_wait(
        self, chat_id: str, coro: Any, on_start: Any = None, on_done: Any = None
    ) -> None:
        """Record wait dispatch and run the supplied turn."""
        self.calls.append(("dispatch_wait", chat_id))
        await coro


def _message(session_key: str = "session-1") -> InboundMessage:
    """Build a normalized test message."""
    return InboundMessage.create(
        channel="cli",
        conversation_id="local",
        sender_id="user",
        content="hello",
        session_key=session_key,
    )


@pytest.mark.asyncio
async def test_default_queue_key_uses_message_route_and_passes_same_message() -> None:
    queue = RecordingQueue()
    coordinator = InboundTurnCoordinator(queue)
    received: list[InboundMessage] = []

    async def handler(message: InboundMessage) -> None:
        received.append(message)

    message = _message()
    await coordinator.submit(message, handler)
    assert queue.calls == [("dispatch", "session-1")]
    assert received == [message]


@pytest.mark.asyncio
async def test_adapter_preserves_cli_queue_key_and_wait_api() -> None:
    queue = RecordingQueue()
    coordinator = InboundTurnCoordinator(queue, queue_key=lambda message: "__cli__")

    async def handler(message: InboundMessage) -> None:
        return None

    await coordinator.submit(_message(), handler, wait=True)
    assert queue.calls == [("dispatch_wait", "__cli__")]


@pytest.mark.asyncio
async def test_empty_queue_key_is_rejected_before_handler_creation() -> None:
    coordinator = InboundTurnCoordinator(RecordingQueue(), queue_key=lambda message: " ")
    called = False

    async def handler(message: InboundMessage) -> None:
        nonlocal called
        called = True

    with pytest.raises(ValueError, match="queue key"):
        await coordinator.submit(_message(), handler)
    assert called is False


def test_cli_mapper_preserves_session_and_interface_metadata() -> None:
    message = build_cli_inbound_message("hello", "bound-session", interface="fallback")
    assert message.channel == CLI_CHANNEL
    assert message.conversation_id == CLI_CONVERSATION_ID
    assert message.sender_id == CLI_SENDER_ID
    assert message.session_key == "bound-session"
    assert message.metadata == {"interface": "fallback"}


@pytest.mark.asyncio
async def test_real_queue_wait_mode_executes_handler_before_return() -> None:
    queue = MessageQueueManager()
    coordinator = InboundTurnCoordinator(queue, queue_key=lambda message: CLI_CONVERSATION_ID)
    received: list[str] = []

    async def handler(message: InboundMessage) -> None:
        received.append(message.content)

    await coordinator.submit(_message(), handler, wait=True)
    assert received == ["hello"]


@pytest.mark.asyncio
async def test_real_preemptive_queue_preserves_cancellation_semantics() -> None:
    queue = MessageQueueManager()
    queue.mode = QueueMode.PREEMPTIVE
    coordinator = InboundTurnCoordinator(queue, queue_key=lambda message: CLI_CONVERSATION_ID)
    started = asyncio.Event()
    cancelled = asyncio.Event()
    second_completed = asyncio.Event()

    async def first_handler(message: InboundMessage) -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def second_handler(message: InboundMessage) -> None:
        second_completed.set()

    first = asyncio.create_task(coordinator.submit(_message(), first_handler))
    await started.wait()
    await coordinator.submit(_message(), second_handler)
    await asyncio.wait_for(cancelled.wait(), timeout=1)
    await asyncio.wait_for(second_completed.wait(), timeout=1)
    await first
