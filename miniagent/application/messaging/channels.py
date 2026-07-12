"""Channel registration and ordered outbound event delivery."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable

from miniagent.contracts.channels import ChannelAdapter
from miniagent.contracts.messages import OutboundEvent


class ChannelRegistrationError(ValueError):
    """A channel identifier is empty or already registered."""


class ChannelNotRegisteredError(LookupError):
    """No outbound adapter exists for the requested channel identifier."""


class ChannelDeliveryError(RuntimeError):
    """An adapter failed while delivering a normalized outbound event."""

    def __init__(self, event: OutboundEvent, cause: BaseException) -> None:
        """Retain the failed event and original adapter error for policy decisions."""
        self.event = event
        self.cause = cause
        super().__init__(
            f"channel {event.target.channel!r} failed to deliver event {event.event_id!r}: {cause}"
        )


class ChannelRegistry:
    """Explicit channel adapter catalog with no imports of concrete channels."""

    def __init__(self, adapters: Iterable[ChannelAdapter] = ()) -> None:
        """Register initial adapters and reject duplicate identifiers."""
        self._adapters: dict[str, ChannelAdapter] = {}
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: ChannelAdapter, *, replace: bool = False) -> None:
        """Register an adapter, optionally replacing an existing implementation."""
        channel_id = adapter.channel_id.strip()
        if not channel_id:
            raise ChannelRegistrationError("channel_id must not be empty")
        if channel_id in self._adapters and not replace:
            raise ChannelRegistrationError(f"channel {channel_id!r} is already registered")
        self._adapters[channel_id] = adapter

    def unregister(self, channel_id: str) -> ChannelAdapter | None:
        """Remove and return an adapter; unknown identifiers are a no-op."""
        return self._adapters.pop(channel_id, None)

    def get(self, channel_id: str) -> ChannelAdapter:
        """Return an adapter or raise a stable lookup error."""
        try:
            return self._adapters[channel_id]
        except KeyError as error:
            raise ChannelNotRegisteredError(f"channel {channel_id!r} is not registered") from error

    def list_channel_ids(self) -> tuple[str, ...]:
        """Return identifiers in deterministic registration order."""
        return tuple(self._adapters)

    async def send(self, event: OutboundEvent) -> None:
        """Route an event and normalize concrete adapter failures."""
        adapter = self.get(event.target.channel)
        try:
            await adapter.send(event)
        except asyncio.CancelledError:
            raise
        except ChannelDeliveryError:
            raise
        except BaseException as error:
            raise ChannelDeliveryError(event, error) from error

    async def send_ordered(self, events: Iterable[OutboundEvent]) -> None:
        """Deliver events sequentially, preserving caller-provided stream order."""
        for event in events:
            await self.send(event)


__all__ = [
    "ChannelDeliveryError",
    "ChannelNotRegisteredError",
    "ChannelRegistrationError",
    "ChannelRegistry",
]
