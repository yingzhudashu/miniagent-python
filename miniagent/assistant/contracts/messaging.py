"""Inbound queue and turn handler protocols used by application coordination."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

from miniagent.assistant.contracts.messages import ChannelTarget, InboundMessage, OutboundEvent

InboundTurnHandler = Callable[[InboundMessage], Awaitable[None]]
QueueKeyResolver = Callable[[InboundMessage], str]


@runtime_checkable
class InboundQueueProtocol(Protocol):
    """Minimal queue surface required by ``InboundTurnCoordinator``."""

    async def dispatch(
        self,
        chat_id: str,
        coro: Any,
        on_start: Any = None,
        on_done: Any = None,
    ) -> None:
        """Schedule a turn according to the queue's configured policy."""
        ...

    async def dispatch_wait(
        self,
        chat_id: str,
        coro: Any,
        on_start: Any = None,
        on_done: Any = None,
    ) -> None:
        """Schedule a turn and wait for completion when supported."""
        ...


@runtime_checkable
class OrderedOutboundDispatcherProtocol(Protocol):
    """Ordered stream bridge shared by synchronous producers and async turns."""

    def publish(self, event: OutboundEvent) -> Any:
        """Schedule an event and return its background task handle."""
        ...

    async def drain(self, target: ChannelTarget) -> None:
        """Wait for queued events targeting one channel conversation."""
        ...


__all__ = [
    "InboundQueueProtocol",
    "InboundTurnHandler",
    "OrderedOutboundDispatcherProtocol",
    "QueueKeyResolver",
]
