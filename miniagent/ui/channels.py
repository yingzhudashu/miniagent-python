"""Channel adapter contract and ordered destination registry."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from miniagent.ui.messages import OutboundEvent


@runtime_checkable
class ChannelAdapter(Protocol):
    @property
    def channel_id(self) -> str: ...

    async def send(self, event: OutboundEvent) -> None: ...


@runtime_checkable
class ChannelRegistryProtocol(Protocol):
    def register(self, adapter: ChannelAdapter, *, replace: bool = False) -> None: ...

    def get(self, channel_id: str) -> ChannelAdapter: ...

    async def send(self, event: OutboundEvent) -> None: ...


class ChannelRegistrationError(ValueError):
    pass


class ChannelNotRegisteredError(LookupError):
    pass


class ChannelDeliveryError(RuntimeError):
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
        channel_id = adapter.channel_id.strip()
        if not channel_id:
            raise ChannelRegistrationError("channel_id must not be empty")
        if channel_id in self._adapters and not replace:
            raise ChannelRegistrationError(f"channel {channel_id!r} is already registered")
        self._adapters[channel_id] = adapter

    def unregister(self, channel_id: str) -> ChannelAdapter | None:
        return self._adapters.pop(channel_id, None)

    def get(self, channel_id: str) -> ChannelAdapter:
        try:
            return self._adapters[channel_id]
        except KeyError as error:
            raise ChannelNotRegisteredError(
                f"channel {channel_id!r} is not registered"
            ) from error

    def list_channel_ids(self) -> tuple[str, ...]:
        return tuple(self._adapters)

    async def send(self, event: OutboundEvent) -> None:
        adapter = self.get(event.target.channel)
        try:
            await adapter.send(event)
        except asyncio.CancelledError:
            raise
        except ChannelDeliveryError:
            raise
        except BaseException as error:
            raise ChannelDeliveryError(event, error) from error


__all__ = [
    "ChannelAdapter",
    "ChannelDeliveryError",
    "ChannelNotRegisteredError",
    "ChannelRegistrationError",
    "ChannelRegistry",
    "ChannelRegistryProtocol",
]
