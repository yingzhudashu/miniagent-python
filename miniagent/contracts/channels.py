"""Platform-neutral channel adapter and registry protocols."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from miniagent.contracts.messages import OutboundEvent


@runtime_checkable
class ChannelAdapter(Protocol):
    """Deliver normalized outbound events to one named channel."""

    @property
    def channel_id(self) -> str:
        """Return the stable channel identifier used by ``ChannelTarget``."""
        ...

    async def send(self, event: OutboundEvent) -> None:
        """Deliver one event or raise when delivery fails."""
        ...


@runtime_checkable
class ChannelRegistryProtocol(Protocol):
    """Lookup and dispatch protocol injected into the application container."""

    def register(self, adapter: ChannelAdapter, *, replace: bool = False) -> None:
        """Register an adapter, optionally replacing the current channel owner."""
        ...

    def get(self, channel_id: str) -> ChannelAdapter:
        """Return the adapter registered for ``channel_id``."""
        ...

    async def send(self, event: OutboundEvent) -> None:
        """Route an event to its target channel."""
        ...


__all__ = ["ChannelAdapter", "ChannelRegistryProtocol"]
