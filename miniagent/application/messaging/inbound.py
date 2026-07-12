"""Queue-compatible coordination for normalized inbound application turns."""

from __future__ import annotations

from typing import Any

from miniagent.contracts.messages import InboundMessage
from miniagent.contracts.messaging import (
    InboundQueueProtocol,
    InboundTurnHandler,
    QueueKeyResolver,
)


def _route_key(message: InboundMessage) -> str:
    """Use the message's collision-safe application route by default."""
    return message.route_key


class InboundTurnCoordinator:
    """Submit normalized messages without changing existing queue semantics."""

    def __init__(
        self,
        queue: InboundQueueProtocol,
        *,
        queue_key: QueueKeyResolver = _route_key,
    ) -> None:
        """Bind a queue and an adapter-specific queue-key policy."""
        self._queue = queue
        self._queue_key = queue_key

    async def submit(
        self,
        message: InboundMessage,
        handler: InboundTurnHandler,
        *,
        wait: bool = False,
        on_start: Any = None,
        on_done: Any = None,
    ) -> None:
        """Dispatch one message through the configured queue.

        ``wait=False`` preserves the current CLI/Feishu enqueue-and-return
        behavior. Callers such as scheduled jobs may request ``wait=True``.
        """
        queue_key = self._queue_key(message).strip()
        if not queue_key:
            raise ValueError("inbound queue key must not be empty")
        turn = handler(message)
        if wait:
            await self._queue.dispatch_wait(queue_key, turn, on_start, on_done)
        else:
            await self._queue.dispatch(queue_key, turn, on_start, on_done)


__all__ = ["InboundTurnCoordinator"]
