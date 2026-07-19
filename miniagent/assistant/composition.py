"""Runtime binding an Agent instance to instance services and UI surfaces."""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import uuid4

from miniagent.agent.events import AgentEvent, AgentEventKind
from miniagent.agent.lifecycle import HealthReport, HealthState, LifecycleManager
from miniagent.agent.runtime import AgentRequest
from miniagent.assistant.spec import AssistantSpec
from miniagent.ui.contracts import UIInput, UIInputKind, UISurface, UITarget

_logger = logging.getLogger(__name__)


class ComposedAssistantRuntime:
    """Thin composition host; business execution remains inside AgentRuntime."""

    def __init__(self, spec: AssistantSpec) -> None:
        if spec.agent_factory is None:
            raise ValueError("composed runtime requires agent_factory")
        self.spec = spec
        self.agent = spec.agent_factory()
        self.surfaces = tuple(factory() for factory in spec.surface_factories)
        self._surface_by_id = {surface.surface_id: surface for surface in self.surfaces}
        if len(self._surface_by_id) != len(self.surfaces):
            raise ValueError("UI surface ids must be unique")
        self.services = tuple(factory() for factory in spec.service_factories)
        self._service_lifecycle = LifecycleManager(self.services)
        self._surface_lifecycle = LifecycleManager(self.surfaces)
        self._targets: dict[str, UITarget] = {}
        self._runs: dict[str, list[tuple[str, asyncio.Task[Any]]]] = {}
        self._input_tasks: set[asyncio.Task[Any]] = set()
        self._started = False
        self._stopping = False
        self._stopped = False
        self._lifecycle_lock = asyncio.Lock()
        self._unsubscribe = self.agent.subscribe(self._render_event)

    async def start(self) -> None:
        """Start Agent, instance services and surfaces in dependency order."""
        async with self._lifecycle_lock:
            if self._started:
                return
            if self._stopping or self._stopped:
                raise RuntimeError("stopped Assistant runtime cannot be restarted")
            try:
                await self.agent.initialize()
                await self.agent.start()
                await self._service_lifecycle.start()
                await self._surface_lifecycle.start()
            except BaseException:
                self._stopping = True
                try:
                    await self._stop_components()
                except BaseException:
                    _logger.exception("Assistant cleanup failed after startup failure")
                finally:
                    self._stopping = False
                    self._stopped = True
                raise
            self._started = True

    async def serve(self) -> None:
        """Consume every surface until completion, then stop the application."""
        await self.start()
        consumers = [
            asyncio.create_task(self._consume(surface), name=f"ui:{surface.surface_id}")
            for surface in self.surfaces
        ]
        self._input_tasks.update(consumers)
        try:
            if consumers:
                await asyncio.gather(*consumers)
            else:
                await asyncio.Event().wait()
        finally:
            await self.stop()

    async def _consume(self, surface: UISurface) -> None:
        try:
            async for input_ in surface.inputs():
                await self.dispatch(input_)
        finally:
            current = asyncio.current_task()
            if current is not None:
                self._input_tasks.discard(current)

    async def dispatch(self, input_: UIInput) -> str | None:
        """Route one normalized UI input without putting channel logic in Agent."""
        if not self._started or self._stopping:
            raise RuntimeError("Assistant runtime is not accepting input")
        session_id = input_.session_id or (
            f"{input_.target.surface_id}:{input_.target.conversation_id}"
        )
        if input_.kind is UIInputKind.CANCEL:
            runs = self._runs.get(session_id, [])
            current = next((item for item in runs if not item[1].done()), None)
            return current[0] if current and await self.agent.cancel(current[0]) else None
        if input_.kind is UIInputKind.COMMAND:
            if self.spec.command_handler is None:
                raise RuntimeError("Assistant spec has no command handler")
            await self.spec.command_handler(input_, self.agent)
            return None
        if input_.kind is UIInputKind.CONFIRMATION:
            if self.spec.command_handler is None:
                raise RuntimeError("Assistant spec has no confirmation handler")
            await self.spec.command_handler(input_, self.agent)
            return None
        run_id = uuid4().hex
        task = asyncio.create_task(
            self.agent.run(
                AgentRequest(
                    input_.content,
                    session_key=session_id,
                    attachments=input_.attachments,
                    metadata=input_.metadata,
                    idempotency_key=input_.idempotency_key,
                    trace_id=input_.trace_id,
                ),
                run_id=run_id,
            ),
            name=f"agent:{run_id}",
        )
        self._targets[run_id] = input_.target
        self._runs.setdefault(session_id, []).append((run_id, task))

        def completed(done: asyncio.Task[Any]) -> None:
            runs = self._runs.get(session_id, [])
            remaining = [item for item in runs if item[0] != run_id]
            if remaining:
                self._runs[session_id] = remaining
            else:
                self._runs.pop(session_id, None)
            if done.cancelled():
                self._targets.pop(run_id, None)
                return
            error = done.exception()
            if error is not None:
                self._targets.pop(run_id, None)
                _logger.debug("Agent run %s failed: %s", run_id, error)

        task.add_done_callback(completed)
        return run_id

    async def _render_event(self, event: AgentEvent) -> None:
        target = self._targets.get(event.run_id)
        if target is None:
            return
        terminal = event.kind in {
            AgentEventKind.RUN_COMPLETED,
            AgentEventKind.RUN_FAILED,
            AgentEventKind.RUN_CANCELLED,
        }
        surface = self._surface_by_id.get(target.surface_id)
        if surface is None:
            if terminal:
                self._targets.pop(event.run_id, None)
            return
        try:
            await surface.render(event, target)
        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.exception(
                "UI surface %s failed to render %s",
                target.surface_id,
                event.kind.value,
            )
        finally:
            if terminal:
                self._targets.pop(event.run_id, None)

    def health(self) -> HealthReport:
        """Aggregate Agent, service and surface health into one snapshot."""
        reports = {
            "agent": self.agent.health(),
            **{
                f"service:{name}": report
                for name, report in self._service_lifecycle.health().items()
            },
            **{
                f"ui:{name}": report
                for name, report in self._surface_lifecycle.health().items()
            },
        }
        state = HealthState.READY if self._started else HealthState.STOPPED
        if any(report.state is HealthState.FAILED for report in reports.values()):
            state = HealthState.FAILED
        elif any(report.state is HealthState.DEGRADED for report in reports.values()):
            state = HealthState.DEGRADED
        return HealthReport(
            state,
            metadata={
                "active_runs": sum(len(runs) for runs in self._runs.values()),
                "targets": len(self._targets),
                "accepting": self._started and not self._stopping,
                "components": {
                    name: report.state.value for name, report in reports.items()
                },
            },
        )

    async def stop(self) -> None:
        """Cancel consumers and stop every component in reverse dependency order."""
        async with self._lifecycle_lock:
            if self._stopped:
                return
            self._stopping = True
            try:
                await self._stop_components()
            finally:
                self._stopping = False
                self._stopped = True

    async def _stop_components(self) -> None:
        """Attempt every owned cleanup and preserve control-flow exceptions."""
        for task in tuple(self._input_tasks):
            task.cancel()
        if self._input_tasks:
            await asyncio.gather(*self._input_tasks, return_exceptions=True)
        errors: list[tuple[str, Exception]] = []
        control_error: BaseException | None = None
        for component, stop in (
            ("surfaces", self._surface_lifecycle.stop),
            ("services", self._service_lifecycle.stop),
            ("agent", self.agent.stop),
        ):
            try:
                await stop()
            except BaseException as error:
                if isinstance(error, Exception):
                    errors.append((component, error))
                elif control_error is None:
                    control_error = error
        self._unsubscribe()
        self._started = False
        self._runs.clear()
        self._targets.clear()
        if control_error is not None:
            raise control_error
        if errors:
            names = ", ".join(name for name, _error in errors)
            raise RuntimeError(
                f"{len(errors)} Assistant component(s) failed to stop: {names}"
            ) from errors[0][1]


__all__ = ["ComposedAssistantRuntime"]
