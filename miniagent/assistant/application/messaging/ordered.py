"""Ordered asynchronous delivery for events produced by synchronous callbacks."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from miniagent.assistant.contracts.channels import ChannelRegistryProtocol
from miniagent.assistant.contracts.messages import ChannelTarget, OutboundEvent


@dataclass(frozen=True, slots=True)
class OutboundDeliveryFailure:
    """One event and the adapter exception observed while delivering it."""

    event: OutboundEvent
    cause: Exception


class OutboundStreamError(RuntimeError):
    """One or more queued events failed before a stream was drained."""

    def __init__(self, failures: tuple[OutboundDeliveryFailure, ...]) -> None:
        """Retain every failure so callers can apply an explicit error policy."""
        self.failures = failures
        super().__init__(f"{len(failures)} outbound event(s) failed during ordered delivery")


class OrderedOutboundDispatcher:
    """Queue events per target while allowing unrelated targets to run concurrently.

    ``publish`` is intentionally synchronous so existing stream callbacks can
    enqueue work without becoming async. Callers must await ``drain`` before
    ending the corresponding turn or sending its terminal event.
    """

    def __init__(self, registry: ChannelRegistryProtocol) -> None:
        """Bind the registry used by background delivery tasks."""
        self._registry = registry
        self._tails: dict[str, asyncio.Task[None]] = {}
        self._failures: dict[str, list[OutboundDeliveryFailure]] = {}

    @staticmethod
    def route_key(target: ChannelTarget) -> str:
        """Return a collision-safe target stream identifier."""
        return f"{target.channel}:{target.conversation_id}"

    def publish(self, event: OutboundEvent) -> asyncio.Task[None]:
        """Schedule one event after the prior event for the same target."""
        loop = asyncio.get_running_loop()
        key = self.route_key(event.target)
        previous = self._tails.get(key)

        async def _deliver() -> None:
            if previous is not None:
                await previous
            try:
                await self._registry.send(event)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self._failures.setdefault(key, []).append(
                    OutboundDeliveryFailure(event, error)
                )

        task = loop.create_task(_deliver())
        self._tails[key] = task
        return task

    async def drain(self, target: ChannelTarget) -> None:
        """Wait for all events currently queued for a target and raise failures."""
        key = self.route_key(target)
        while True:
            tail = self._tails.get(key)
            if tail is None:
                break
            try:
                await tail
            finally:
                if self._tails.get(key) is tail:
                    self._tails.pop(key, None)
            if self._tails.get(key) is None:
                break

        failures = tuple(self._failures.pop(key, ()))
        if failures:
            raise OutboundStreamError(failures)

    def pending_route_count(self) -> int:
        """Return the number of target streams retaining a delivery tail."""
        return len(self._tails)


__all__ = [
    "OrderedOutboundDispatcher",
    "OutboundDeliveryFailure",
    "OutboundStreamError",
]
