"""Minimal channel adapter used by messaging service tests."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from miniagent.contracts import OutboundEvent


@dataclass(frozen=True, slots=True)
class FunctionChannelAdapter:
    """Adapt an async test callback to the production ChannelAdapter contract."""

    channel_id: str
    sender: Callable[[OutboundEvent], Awaitable[None]]

    async def send(self, event: OutboundEvent) -> None:
        await self.sender(event)


__all__ = ["FunctionChannelAdapter"]
