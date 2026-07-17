"""Ordered outbound dispatcher ordering, failure and cancellation tests."""

from __future__ import annotations

import asyncio

import pytest

from miniagent.assistant.application.messaging.ordered import (
    OrderedOutboundDispatcher,
    OutboundStreamError,
)
from miniagent.ui.channels import ChannelRegistry
from miniagent.ui.messages import ChannelTarget, OutboundEvent, OutboundEventKind
from tests.support.channel import FunctionChannelAdapter


def _event(conversation_id: str, content: str) -> OutboundEvent:
    """Build a deterministic target stream event for tests."""
    return OutboundEvent.create(
        kind=OutboundEventKind.THINKING_DELTA,
        target=ChannelTarget("cli", conversation_id),
        content=content,
    )


@pytest.mark.asyncio
async def test_same_target_delivery_is_strictly_ordered() -> None:
    """A later fragment must not overtake a blocked prior fragment."""
    release_first = asyncio.Event()
    delivered: list[str] = []

    async def sender(event: OutboundEvent) -> None:
        if event.content == "first":
            await release_first.wait()
        delivered.append(event.content)

    dispatcher = OrderedOutboundDispatcher(
        ChannelRegistry([FunctionChannelAdapter("cli", sender)])
    )
    first = _event("session-1", "first")
    second = _event("session-1", "second")
    dispatcher.publish(first)
    dispatcher.publish(second)
    await asyncio.sleep(0)
    assert delivered == []

    release_first.set()
    await dispatcher.drain(first.target)
    assert delivered == ["first", "second"]
    assert dispatcher.pending_route_count() == 0


@pytest.mark.asyncio
async def test_unrelated_targets_can_deliver_concurrently() -> None:
    """A blocked session must not delay another session's stream."""
    release_a = asyncio.Event()
    delivered: list[str] = []

    async def sender(event: OutboundEvent) -> None:
        if event.target.conversation_id == "A":
            await release_a.wait()
        delivered.append(event.target.conversation_id)

    dispatcher = OrderedOutboundDispatcher(
        ChannelRegistry([FunctionChannelAdapter("cli", sender)])
    )
    event_a = _event("A", "a")
    event_b = _event("B", "b")
    dispatcher.publish(event_a)
    dispatcher.publish(event_b)
    await dispatcher.drain(event_b.target)
    assert delivered == ["B"]

    release_a.set()
    await dispatcher.drain(event_a.target)
    assert delivered == ["B", "A"]


@pytest.mark.asyncio
async def test_failure_is_aggregated_without_dropping_later_events() -> None:
    """A failed fragment remains observable while the stream continues."""
    delivered: list[str] = []

    async def sender(event: OutboundEvent) -> None:
        if event.content == "bad":
            raise OSError("render failed")
        delivered.append(event.content)

    dispatcher = OrderedOutboundDispatcher(
        ChannelRegistry([FunctionChannelAdapter("cli", sender)])
    )
    bad = _event("session-2", "bad")
    dispatcher.publish(bad)
    dispatcher.publish(_event("session-2", "good"))

    with pytest.raises(OutboundStreamError) as caught:
        await dispatcher.drain(bad.target)
    assert delivered == ["good"]
    assert len(caught.value.failures) == 1
    assert caught.value.failures[0].event is bad
    assert isinstance(caught.value.failures[0].cause, RuntimeError)
    assert dispatcher.pending_route_count() == 0


@pytest.mark.asyncio
async def test_delivery_cancellation_propagates_from_drain() -> None:
    """Cancellation must not be converted into an ordinary delivery failure."""
    started = asyncio.Event()

    async def sender(_event: OutboundEvent) -> None:
        started.set()
        await asyncio.Event().wait()

    dispatcher = OrderedOutboundDispatcher(
        ChannelRegistry([FunctionChannelAdapter("cli", sender)])
    )
    event = _event("session-3", "wait")
    task = dispatcher.publish(event)
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await dispatcher.drain(event.target)
    assert dispatcher.pending_route_count() == 0


def test_publish_requires_a_running_event_loop() -> None:
    """A synchronous caller outside asyncio must receive a clear failure."""
    dispatcher = OrderedOutboundDispatcher(ChannelRegistry())
    with pytest.raises(RuntimeError, match="running event loop"):
        dispatcher.publish(_event("session-4", "orphan"))
