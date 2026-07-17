"""Lifecycle adapter for the existing Feishu WebSocket runtime."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from miniagent.agent.lifecycle import HealthReport, HealthState

if TYPE_CHECKING:
    from miniagent.assistant.contracts.runtime import FeishuRuntimeProtocol

HandlerFactory = Callable[[dict[str, Any] | None], Any]
UserStatusCallback = Callable[[str], None]


@dataclass(slots=True)
class FeishuRuntimeLifecycleService:
    """Place ``FeishuRuntime`` in the process lifecycle without owning transport logic."""

    enabled: bool
    runtime: FeishuRuntimeProtocol | None = field(repr=False)
    handler_factory: HandlerFactory | None = field(repr=False)
    state: dict[str, Any] | None = field(default=None, repr=False)
    user_status: UserStatusCallback | None = field(default=None, repr=False)
    name: str = field(default="feishu", init=False)
    _state: HealthState = field(default=HealthState.STOPPED, init=False, repr=False)
    _detail: str = field(default="", init=False, repr=False)
    _start_attempted: bool = field(default=False, init=False, repr=False)

    async def initialize(self) -> None:
        """Validate dependencies only when the Feishu channel is enabled."""
        if not self.enabled:
            self._state = HealthState.STOPPED
            self._detail = "disabled"
            return
        if self.runtime is None:
            self._state = HealthState.FAILED
            self._detail = "Feishu runtime is unavailable"
            raise RuntimeError(self._detail)
        if not callable(self.handler_factory):
            self._state = HealthState.FAILED
            self._detail = "Feishu handler factory is unavailable"
            raise RuntimeError(self._detail)
        self._state = HealthState.STOPPED
        self._detail = "initialized"

    async def start(self) -> None:
        """Delegate startup and preserve runtime decisions such as missing credentials."""
        if not self.enabled:
            self._state = HealthState.STOPPED
            self._detail = "disabled"
            return
        if self.runtime is None or not callable(self.handler_factory):
            self._state = HealthState.FAILED
            self._detail = "Feishu lifecycle service was not initialized"
            raise RuntimeError(self._detail)
        if self._start_attempted and self._runtime_is_running():
            self._state = HealthState.READY
            self._detail = ""
            return

        self._state = HealthState.STARTING
        self._detail = ""
        self._start_attempted = True
        try:
            self.runtime.start(
                self.handler_factory,
                self.state,
                user_status=self.user_status,
            )
        except BaseException as error:
            self._state = HealthState.FAILED
            self._detail = str(error)
            raise

        if self._runtime_is_running():
            self._state = HealthState.READY
        elif self._state is not HealthState.DEGRADED:
            # Missing credentials and an already-owned inbound lock are intentional
            # non-fatal decisions made by FeishuRuntime.start().
            self._state = HealthState.STOPPED
            self._detail = "runtime not running"

    async def stop(self) -> None:
        """Await the runtime's authoritative asynchronous transport cleanup."""
        if not self.enabled or not self._start_attempted or self.runtime is None:
            self._state = HealthState.STOPPED
            self._detail = "disabled" if not self.enabled else "not started"
            return

        try:
            await self.runtime.stop_async()
        except BaseException as error:
            self._state = HealthState.FAILED
            self._detail = str(error)
            # Keep ownership state intact so a subsequent explicit stop can retry.
            raise

        self._start_attempted = False
        self._state = HealthState.STOPPED
        self._detail = ""

    async def activate(self) -> None:
        """Enable and start the runtime from the interactive command surface."""
        self.enabled = True
        await self.initialize()
        await self.start()

    async def deactivate(self) -> None:
        """Stop the runtime and keep it disabled until explicitly activated."""
        await self.stop()
        self.enabled = False
        self._detail = "disabled"

    def health(self) -> HealthReport:
        """Return a non-blocking snapshot of adapter and runtime state."""
        if not self.enabled:
            return HealthReport(HealthState.STOPPED, "disabled")
        if self._state is HealthState.FAILED:
            return HealthReport(HealthState.FAILED, self._detail)
        if self.runtime is None or not self._start_attempted:
            return HealthReport(HealthState.STOPPED, self._detail)
        try:
            running = bool(self.runtime.is_running())
        except Exception as error:
            return HealthReport(HealthState.DEGRADED, f"health probe failed: {error}")
        if running:
            return HealthReport(HealthState.READY)
        return HealthReport(HealthState.STOPPED, "runtime not running")

    def _runtime_is_running(self) -> bool:
        """Probe runtime liveness without allowing diagnostics to break startup."""
        if self.runtime is None:
            return False
        try:
            return bool(self.runtime.is_running())
        except Exception as error:
            self._state = HealthState.DEGRADED
            self._detail = f"health probe failed: {error}"
            return False


__all__ = ["FeishuRuntimeLifecycleService"]
