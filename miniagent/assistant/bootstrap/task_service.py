"""Lifecycle adapter for one cooperative asyncio background task."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field

from miniagent.assistant.contracts.lifecycle import HealthReport, HealthState

TaskStarter = Callable[[], asyncio.Task | None]
StopSignaler = Callable[[], None]


@dataclass(slots=True)
class AsyncTaskLifecycleService:
    """Manage a task using its existing starter and cooperative stop signal."""

    name: str
    starter: TaskStarter = field(repr=False)
    signal_stop: StopSignaler = field(repr=False)
    _task: asyncio.Task | None = field(default=None, init=False, repr=False)
    _state: HealthState = field(default=HealthState.STOPPED, init=False)

    async def initialize(self) -> None:
        """Validate service identity without starting background work."""
        if not self.name.strip():
            raise ValueError("task lifecycle service name must not be empty")

    async def start(self) -> None:
        """Start the wrapped task once and publish its health state."""
        if self._task is not None and not self._task.done():
            return
        self._state = HealthState.STARTING
        try:
            self._task = self.starter()
        except BaseException:
            self._state = HealthState.FAILED
            raise
        self._state = HealthState.READY

    async def stop(self) -> None:
        """Signal, cancel and await the wrapped task; repeated calls are safe."""
        self.signal_stop()
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._state = HealthState.STOPPED

    def health(self) -> HealthReport:
        """Return task liveness without awaiting it."""
        task = self._task
        if self._state is HealthState.READY and task is not None and task.done():
            if task.cancelled():
                return HealthReport(HealthState.STOPPED, "task cancelled")
            if task.exception() is not None:
                return HealthReport(HealthState.FAILED, str(task.exception()))
            return HealthReport(HealthState.STOPPED, "task completed")
        return HealthReport(self._state)


__all__ = ["AsyncTaskLifecycleService"]
