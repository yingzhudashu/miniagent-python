"""Channel adapter contract and ordered destination registry."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from miniagent.ui.messages import OutboundEvent


@runtime_checkable
class ChannelAdapter(Protocol):
    """Transport adapter capable of delivering channel-neutral events."""

    @property
    def channel_id(self) -> str: ...

    async def send(self, event: OutboundEvent) -> None: ...


@runtime_checkable
class ChannelRegistryProtocol(Protocol):
    """Registration and delivery surface used by the application layer."""

    def register(self, adapter: ChannelAdapter, *, replace: bool = False) -> None: ...

    def get(self, channel_id: str) -> ChannelAdapter: ...

    async def send(self, event: OutboundEvent) -> None: ...


class ChannelRegistrationError(ValueError):
    """A channel adapter has an empty or duplicate identifier."""


class ChannelNotRegisteredError(LookupError):
    """Delivery targeted a channel without a registered adapter."""


class ChannelDeliveryError(RuntimeError):
    """Wrap an ordinary adapter failure while retaining the failed event."""

    def __init__(self, event: OutboundEvent, cause: BaseException) -> None:
        self.event = event
        self.cause = cause
        super().__init__(
            f"channel {event.target.channel!r} failed to deliver event {event.event_id!r}: {cause}"
        )


class ChannelRegistry:
    """Explicit adapter catalog; delivery failure never re-executes Agent work."""

    def __init__(self, adapters: Iterable[ChannelAdapter] = ()) -> None:
        self._adapters: dict[str, ChannelAdapter] = {}
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: ChannelAdapter, *, replace: bool = False) -> None:
        """Register an adapter, requiring explicit replacement of duplicates."""
        channel_id = adapter.channel_id.strip()
        if not channel_id:
            raise ChannelRegistrationError("channel_id must not be empty")
        if channel_id in self._adapters and not replace:
            raise ChannelRegistrationError(f"channel {channel_id!r} is already registered")
        self._adapters[channel_id] = adapter

    def unregister(self, channel_id: str) -> ChannelAdapter | None:
        """Remove and return an adapter when present."""
        return self._adapters.pop(channel_id, None)

    def get(self, channel_id: str) -> ChannelAdapter:
        """Return an adapter or raise a stable not-registered error."""
        try:
            return self._adapters[channel_id]
        except KeyError as error:
            raise ChannelNotRegisteredError(
                f"channel {channel_id!r} is not registered"
            ) from error

    def list_channel_ids(self) -> tuple[str, ...]:
        """List identifiers in deterministic registration order."""
        return tuple(self._adapters)

    async def send(self, event: OutboundEvent) -> None:
        """Deliver once and wrap ordinary transport failures with event context."""
        adapter = self.get(event.target.channel)
        try:
            await adapter.send(event)
        except asyncio.CancelledError:
            raise
        except ChannelDeliveryError:
            raise
        except Exception as error:
            raise ChannelDeliveryError(event, error) from error


__all__ = [
    "ChannelAdapter",
    "ChannelDeliveryError",
    "ChannelNotRegisteredError",
    "ChannelRegistrationError",
    "ChannelRegistry",
    "ChannelRegistryProtocol",
]
