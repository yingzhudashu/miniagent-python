"""Channel registry routing, ordering and error semantics."""

from __future__ import annotations

import asyncio

import pytest

from miniagent.assistant.application.messaging.channels import (
    ChannelDeliveryError,
    ChannelNotRegisteredError,
    ChannelRegistrationError,
    ChannelRegistry,
)
from miniagent.assistant.contracts.channels import ChannelAdapter
from miniagent.assistant.contracts.messages import (
    ChannelTarget,
    OutboundEvent,
    OutboundEventKind,
)
from tests.channel_helpers import FunctionChannelAdapter


def _event(channel: str, content: str, sequence: int = 0) -> OutboundEvent:
    """Create a deterministic outbound event for routing tests."""
    return OutboundEvent.create(
        kind=OutboundEventKind.FINAL,
        target=ChannelTarget(channel, "conversation-1"),
        content=content,
        sequence=sequence,
    )


@pytest.mark.asyncio
async def test_registry_routes_to_async_event_sender() -> None:
    delivered: list[str] = []

    async def sender(event: OutboundEvent) -> None:
        delivered.append(event.content)

    adapter = FunctionChannelAdapter("feishu", sender)
    registry = ChannelRegistry([adapter])
    await registry.send(_event("feishu", "done"))
    assert delivered == ["done"]
    assert isinstance(adapter, ChannelAdapter)


@pytest.mark.asyncio
async def test_ordered_delivery_never_overtakes_prior_event() -> None:
    delivered: list[tuple[int, str]] = []

    async def sender(event: OutboundEvent) -> None:
        if event.sequence == 1:
            await asyncio.sleep(0.01)
        delivered.append((event.sequence, event.content))

    registry = ChannelRegistry([FunctionChannelAdapter("cli", sender)])
    for event in (_event("cli", "first", 1), _event("cli", "second", 2)):
        await registry.send(event)
    assert delivered == [(1, "first"), (2, "second")]


def test_duplicate_registration_requires_explicit_replace() -> None:
    async def sender(_event: OutboundEvent) -> None:
        return None

    first = FunctionChannelAdapter("cli", sender)
    second = FunctionChannelAdapter("cli", sender)
    registry = ChannelRegistry([first])

    with pytest.raises(ChannelRegistrationError, match="already registered"):
        registry.register(second)
    registry.register(second, replace=True)
    assert registry.get("cli") is second
    assert registry.list_channel_ids() == ("cli",)
    assert registry.unregister("cli") is second
    assert registry.unregister("cli") is None


def test_empty_or_missing_channel_has_stable_error() -> None:
    async def sender(_event: OutboundEvent) -> None:
        return None

    registry = ChannelRegistry()
    with pytest.raises(ChannelRegistrationError, match="empty"):
        registry.register(FunctionChannelAdapter(" ", sender))
    with pytest.raises(ChannelNotRegisteredError, match="not registered"):
        registry.get("missing")


@pytest.mark.asyncio
async def test_adapter_error_is_wrapped_with_failed_event() -> None:
    async def sender(event: OutboundEvent) -> None:
        raise OSError("offline")

    event = _event("feishu", "hello")
    registry = ChannelRegistry([FunctionChannelAdapter("feishu", sender)])
    with pytest.raises(ChannelDeliveryError) as caught:
        await registry.send(event)
    assert caught.value.event is event
    assert isinstance(caught.value.cause, OSError)


@pytest.mark.asyncio
async def test_delivery_cancellation_is_not_wrapped() -> None:
    async def sender(event: OutboundEvent) -> None:
        raise asyncio.CancelledError

    registry = ChannelRegistry([FunctionChannelAdapter("cli", sender)])
    with pytest.raises(asyncio.CancelledError):
        await registry.send(_event("cli", "cancel"))
