"""Reusable queue-backed CLI, TUI and Feishu surface implementations."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from miniagent.agent.events import AgentEvent
from miniagent.agent.lifecycle import HealthReport, HealthState
from miniagent.ui.contracts import UIInput, UITarget

EventRenderer = Callable[[AgentEvent, UITarget], Awaitable[None] | None]


class QueueUISurface:
    """Backpressure-aware surface base usable by transport-specific receivers."""

    def __init__(
        self,
        surface_id: str,
        *,
        renderer: EventRenderer | None = None,
        queue_size: int = 256,
    ) -> None:
        if not surface_id.strip():
            raise ValueError("surface_id must not be empty")
        if queue_size < 1:
            raise ValueError("queue_size must be at least 1")
        self.surface_id = surface_id
        self.name = surface_id
        self._renderer = renderer
        self._queue: asyncio.Queue[UIInput | None] = asyncio.Queue(queue_size)
        self._state = HealthState.STOPPED
        self._rendered = 0

    async def initialize(self) -> None:
        """Move the surface into its initialized, not-yet-ready state."""
        self._state = HealthState.STARTING

    async def start(self) -> None:
        """Accept input publication and event rendering."""
        self._state = HealthState.READY

    async def stop(self) -> None:
        """Stop input consumption and wake a blocked iterator."""
        if self._state is HealthState.STOPPED:
            return
        self._state = HealthState.STOPPED
        try:
            self._queue.put_nowait(None)
        except asyncio.QueueFull:
            self._queue.get_nowait()
            self._queue.put_nowait(None)

    def health(self) -> HealthReport:
        """Return queue depth and rendered-event counters without blocking."""
        return HealthReport(
            self._state,
            metadata={"queued_inputs": self._queue.qsize(), "rendered": self._rendered},
        )

    async def publish(self, input_: UIInput) -> None:
        """Queue one input after validating lifecycle and surface ownership."""
        if self._state is not HealthState.READY:
            raise RuntimeError(f"UI surface {self.surface_id!r} is not ready")
        if input_.target.surface_id != self.surface_id:
            raise ValueError("UI input target does not match surface")
        await self._queue.put(input_)

    async def inputs(self):
        """Yield queued inputs until :meth:`stop` publishes the sentinel."""
        while True:
            item = await self._queue.get()
            if item is None:
                return
            yield item

    async def render(self, event: AgentEvent, target: UITarget) -> None:
        """Validate the target and invoke the optional transport renderer."""
        if target.surface_id != self.surface_id:
            raise ValueError("UI target does not match surface")
        if self._renderer is not None:
            value = self._renderer(event, target)
            if inspect.isawaitable(value):
                await value
        self._rendered += 1


class CLISurface(QueueUISurface):
    """Line-oriented CLI surface; parsing remains separate from Agent execution."""

    def __init__(
        self,
        *,
        renderer: EventRenderer | None = None,
        output: Callable[[str], Any] = print,
        queue_size: int = 256,
    ) -> None:
        super().__init__("cli", renderer=renderer, queue_size=queue_size)
        self._output = output

    async def render(self, event: AgentEvent, target: UITarget) -> None:
        """Render through the injected renderer or the line-oriented fallback."""
        if target.surface_id != self.surface_id:
            raise ValueError("UI target does not match surface")
        if self._renderer is not None:
            await super().render(event, target)
            return
        text = event.payload.get("text") or event.payload.get("reply")
        if text:
            self._output(str(text))
        self._rendered += 1


class TUISurface(QueueUISurface):
    """Fullscreen terminal surface boundary used by prompt-toolkit adapters."""

    def __init__(self, renderer: EventRenderer, *, queue_size: int = 256) -> None:
        super().__init__("tui", renderer=renderer, queue_size=queue_size)


class FeishuSurface(QueueUISurface):
    """Feishu WebSocket/card surface boundary; SDK work stays in its renderer."""

    def __init__(self, renderer: EventRenderer, *, queue_size: int = 256) -> None:
        super().__init__("feishu", renderer=renderer, queue_size=queue_size)


__all__ = ["CLISurface", "FeishuSurface", "QueueUISurface", "TUISurface"]
