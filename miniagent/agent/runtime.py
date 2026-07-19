"""Stable object-oriented facade for the high-quality answer pipeline."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4

from miniagent.agent.events import AgentEvent, AgentEventKind
from miniagent.agent.extensions import AgentExtension
from miniagent.agent.lifecycle import (
    HealthReport,
    HealthState,
    LifecycleManager,
    LifecyclePhase,
)
from miniagent.agent.ports.knowledge import KnowledgeRegistryProtocol
from miniagent.agent.ports.memory import MemoryRuntimeProtocol
from miniagent.agent.settings import AgentSettings, use_agent_settings
from miniagent.agent.types.agent import AgentRunOptions, AgentRunResult, ToolMonitorProtocol
from miniagent.agent.types.confirmation import ConfirmationResult
from miniagent.agent.types.planning import StructuredPlan
from miniagent.agent.types.tool import Toolbox, ToolRegistryProtocol
from miniagent.llm.gateway import LLMGateway

AgentResult = AgentRunResult
AgentEventSubscriber = Callable[[AgentEvent], Awaitable[None] | None]

_logger = logging.getLogger(__name__)


@runtime_checkable
class AgentObserver(Protocol):
    """Receives semantic progress events without coupling Agent to a UI."""

    async def on_thinking(
        self,
        text: str,
        streaming: bool,
        header: str,
        *,
        full_record: str | None = None,
        reset: bool = False,
        is_last_step: bool = False,
    ) -> None: ...

    def on_tool_call(self, name: str, arguments: str, result: str) -> None: ...

    async def on_tool_finish(
        self,
        name: str,
        arguments: str,
        result: str,
        success: bool,
        *,
        thinking_header: str | None = None,
    ) -> None: ...

    async def on_plan(self, plan: StructuredPlan) -> ConfirmationResult: ...

    async def on_reflection(self, reflection: Any) -> None: ...


@dataclass(frozen=True, slots=True)
class _CoreAgentServices:
    """Injected capabilities required by an Agent instance."""

    llm: LLMGateway
    settings: AgentSettings
    registry: ToolRegistryProtocol
    memory: MemoryRuntimeProtocol
    knowledge: KnowledgeRegistryProtocol
    monitor: ToolMonitorProtocol | None = None
    observer: AgentObserver | None = None
    clawhub: Any | None = None
    clarifier: Any | None = None
    confirmation_channel: Any | None = None
    tool_semaphore: asyncio.Semaphore | None = None
    runner: Any | None = None


@dataclass(frozen=True, slots=True)
class AgentRequest:
    """Immutable input for one complete classify-to-reflect answer turn."""

    user_input: str
    session_key: str | None = None
    toolboxes: tuple[Toolbox, ...] = ()
    system_prompt: str | None = None
    options: AgentRunOptions | None = None
    config: dict[str, Any] | None = None
    skip_planning: bool = False
    attachments: tuple[Any, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    idempotency_key: str | None = None
    trace_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "attachments", tuple(self.attachments))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


def _method(observer: AgentObserver | None, name: str) -> Any | None:
    value = getattr(observer, name, None) if observer is not None else None
    return value if callable(value) else None


class _CoreAgent:
    """Internal adapter from AgentRuntime requests to the established phase engine."""

    def __init__(self, services: _CoreAgentServices) -> None:
        self._services = services

    async def run(self, request: AgentRequest) -> AgentResult:
        """Run one request through the canonical normalized Agent turn."""
        observer = self._services.observer
        on_reflection = _method(observer, "on_reflection")

        async def reflection_callback(value: Any) -> None:
            if on_reflection is None:
                return
            result = on_reflection(value)
            if inspect.isawaitable(result):
                await result

        with use_agent_settings(self._services.settings):
            if self._services.runner is not None:
                return await self._services.runner(
                    request.user_input,
                    registry=self._services.registry,
                    memory=self._services.memory,
                    knowledge_registry=self._services.knowledge,
                    client=self._services.llm,
                    monitor=self._services.monitor,
                    toolboxes=list(request.toolboxes),
                    agent_config=request.config,
                    options=request.options,
                    system_prompt=request.system_prompt,
                    skip_planning=request.skip_planning,
                    on_tool_call=_method(observer, "on_tool_call"),
                    on_tool_finish=_method(observer, "on_tool_finish"),
                    on_plan=_method(observer, "on_plan"),
                    on_thinking=_method(observer, "on_thinking"),
                    on_reflection=reflection_callback if on_reflection is not None else None,
                    clawhub=self._services.clawhub,
                    clarifier=self._services.clarifier,
                    session_key=request.session_key,
                    confirmation_channel=self._services.confirmation_channel,
                    tool_semaphore=self._services.tool_semaphore,
                )

            from miniagent.agent.agent import _AgentTurnContext, _run_agent_turn

            return await _run_agent_turn(
                _AgentTurnContext(
                    user_input=request.user_input,
                    registry=self._services.registry,
                    memory=self._services.memory,
                    knowledge_registry=self._services.knowledge,
                    client=self._services.llm,
                    monitor=self._services.monitor,
                    toolboxes=request.toolboxes,
                    agent_config=dict(request.config) if request.config is not None else None,
                    options=request.options,
                    system_prompt=request.system_prompt,
                    skip_planning=request.skip_planning,
                    on_tool_call=_method(observer, "on_tool_call"),
                    on_tool_finish=_method(observer, "on_tool_finish"),
                    on_plan=_method(observer, "on_plan"),
                    on_thinking=_method(observer, "on_thinking"),
                    on_reflection=reflection_callback if on_reflection is not None else None,
                    clawhub=self._services.clawhub,
                    clarifier=self._services.clarifier,
                    session_key=request.session_key,
                    confirmation_channel=self._services.confirmation_channel,
                    tool_semaphore=self._services.tool_semaphore,
                )
            )


@dataclass(frozen=True, slots=True)
class AgentSpec:
    """Dependencies and runtime policy for one isolated Agent instance."""

    settings: AgentSettings
    registry: ToolRegistryProtocol
    memory: MemoryRuntimeProtocol
    knowledge: KnowledgeRegistryProtocol
    monitor: ToolMonitorProtocol | None = None
    observer: AgentObserver | None = None
    clawhub: Any | None = None
    clarifier: Any | None = None
    confirmation_channel: Any | None = None
    tool_semaphore: asyncio.Semaphore | None = None
    runner: Any | None = None
    max_parallel_sessions: int = 4
    shutdown_timeout: float = 5.0
    owns_llm: bool = True
    owns_memory: bool = True

    def __post_init__(self) -> None:
        if self.max_parallel_sessions < 1:
            raise ValueError("max_parallel_sessions must be at least 1")
        if self.shutdown_timeout < 0:
            raise ValueError("shutdown_timeout must not be negative")

    def _services(self, llm: LLMGateway, observer: AgentObserver) -> _CoreAgentServices:
        """Materialize execution dependencies behind the V4 facade."""
        return _CoreAgentServices(
            llm=llm,
            settings=self.settings,
            registry=self.registry,
            memory=self.memory,
            knowledge=self.knowledge,
            monitor=self.monitor,
            observer=observer,
            clawhub=self.clawhub,
            clarifier=self.clarifier,
            confirmation_channel=self.confirmation_channel,
            tool_semaphore=self.tool_semaphore,
            runner=self.runner,
        )


class _RuntimeObserver:
    """Translate execution callbacks into the ordered public AgentEvent stream."""

    def __init__(
        self,
        runtime: AgentRuntime,
        run_id: str,
        session_id: str,
        trace_id: str,
        delegate: AgentObserver | None = None,
    ):
        self.runtime = runtime
        self.run_id = run_id
        self.session_id = session_id
        self.trace_id = trace_id
        self.delegate = delegate if delegate is not None else runtime.spec.observer

    async def on_thinking(self, text: str, streaming: bool, header: str, **kwargs: Any) -> None:
        kind = AgentEventKind.THINKING_DELTA if streaming else AgentEventKind.THINKING_FINAL
        await self.runtime._emit(
            kind,
            self.run_id,
            self.session_id,
            self.trace_id,
            {"text": text, "header": header, **kwargs},
        )
        callback = _method(self.delegate, "on_thinking")
        if callback is not None:
            await callback(text, streaming, header, **kwargs)

    def on_tool_call(self, name: str, arguments: str, result: str) -> None:
        self.runtime._emit_nowait(
            AgentEventKind.TOOL_STARTED,
            self.run_id,
            self.session_id,
            self.trace_id,
            {"name": name, "arguments": arguments, "result": result},
        )
        callback = _method(self.delegate, "on_tool_call")
        if callback is not None:
            callback(name, arguments, result)

    async def on_tool_finish(
        self,
        name: str,
        arguments: str,
        result: str,
        success: bool,
        **kwargs: Any,
    ) -> None:
        await self.runtime._emit(
            AgentEventKind.TOOL_FINISHED,
            self.run_id,
            self.session_id,
            self.trace_id,
            {
                "name": name,
                "arguments": arguments,
                "result": result,
                "success": success,
                **kwargs,
            },
        )
        callback = _method(self.delegate, "on_tool_finish")
        if callback is not None:
            await callback(name, arguments, result, success, **kwargs)

    async def on_plan(self, plan: StructuredPlan) -> ConfirmationResult:
        await self.runtime._emit(
            AgentEventKind.CONFIRMATION_REQUIRED,
            self.run_id,
            self.session_id,
            self.trace_id,
            {"stage": "plan", "plan": plan},
        )
        callback = _method(self.delegate, "on_plan")
        if callback is None:
            return ConfirmationResult.confirm()
        return await callback(plan)

    async def on_reflection(self, reflection: Any) -> None:
        await self.runtime._emit(
            AgentEventKind.REFLECTION,
            self.run_id,
            self.session_id,
            self.trace_id,
            {"reflection": reflection},
        )
        callback = _method(self.delegate, "on_reflection")
        if callback is not None:
            value = callback(reflection)
            if inspect.isawaitable(value):
                await value


class AgentRuntime:
    """Lifecycle-owned, event-driven Agent runtime with per-session serialization."""

    def __init__(
        self,
        spec: AgentSpec,
        llm: LLMGateway,
        extensions: tuple[AgentExtension, ...] = (),
    ) -> None:
        self.spec = spec
        self.llm = llm
        self.extensions = tuple(extensions)
        self._lifecycle = LifecycleManager(self.extensions)
        self._accepting = False
        self._active: dict[str, asyncio.Task[AgentResult]] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._session_lock_users: dict[str, int] = {}
        self._session_slots = asyncio.Semaphore(spec.max_parallel_sessions)
        self._subscribers: dict[int, AgentEventSubscriber] = {}
        self._sequences: dict[str, int] = {}
        self._delivery_tasks: set[asyncio.Task[None]] = set()
        self._next_subscriber = 0
        self._stop_lock = asyncio.Lock()
        for extension in self.extensions:
            bind = getattr(extension, "bind", None)
            if callable(bind):
                bind(self)

    @property
    def phase(self) -> LifecyclePhase:
        """Current aggregate lifecycle phase for the runtime and extensions."""
        return self._lifecycle.phase

    @property
    def active_run_ids(self) -> tuple[str, ...]:
        """Snapshot the run identifiers that can currently be cancelled."""
        return tuple(self._active)

    async def initialize(self) -> None:
        """Initialize owned LLM resources and extensions with rollback."""
        try:
            if self.spec.owns_llm:
                initialize = getattr(self.llm, "initialize", None)
                if callable(initialize):
                    await initialize()
            await self._lifecycle.initialize()
        except BaseException:
            if self.spec.owns_llm:
                try:
                    await self._stop_llm()
                except BaseException:
                    _logger.exception("Failed to close owned LLM after initialization failure")
            raise

    async def start(self) -> None:
        """Start owned resources and begin accepting requests."""
        try:
            if self.spec.owns_llm:
                start = getattr(self.llm, "start", None)
                if callable(start):
                    await start()
            await self._lifecycle.start()
        except BaseException:
            if self.spec.owns_llm:
                try:
                    await self._stop_llm()
                except BaseException:
                    _logger.exception("Failed to close owned LLM after startup failure")
            raise
        self._accepting = True

    def subscribe(self, subscriber: AgentEventSubscriber) -> Callable[[], None]:
        """Subscribe to semantic events and return an idempotent unsubscribe callback."""
        key = self._next_subscriber
        self._next_subscriber += 1
        self._subscribers[key] = subscriber

        def unsubscribe() -> None:
            self._subscribers.pop(key, None)

        return unsubscribe

    async def events(self) -> AsyncIterator[AgentEvent]:
        """Expose events as an async iterator for UI surfaces."""
        queue: asyncio.Queue[AgentEvent] = asyncio.Queue()
        unsubscribe = self.subscribe(queue.put_nowait)
        try:
            while True:
                yield await queue.get()
        finally:
            unsubscribe()

    async def run(
        self,
        request: AgentRequest,
        *,
        run_id: str | None = None,
        trace_id: str | None = None,
    ) -> AgentResult:
        """Execute one request while serializing other work for the same session."""
        if not self._accepting or self.phase is not LifecyclePhase.READY:
            raise RuntimeError("AgentRuntime is not ready")
        actual_run_id = run_id or uuid4().hex
        if actual_run_id in self._active:
            raise ValueError(f"run_id already active: {actual_run_id}")
        actual_trace_id = trace_id or request.trace_id or f"trace-{uuid4().hex}"
        session_id = request.session_key or "default"
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("AgentRuntime.run requires an asyncio task")
        self._active[actual_run_id] = task
        try:
            return await self._run_locked(
                request, actual_run_id, session_id, actual_trace_id
            )
        finally:
            self._active.pop(actual_run_id, None)
            self._sequences.pop(actual_run_id, None)

    async def _run_locked(
        self,
        request: AgentRequest,
        run_id: str,
        session_id: str,
        trace_id: str,
    ) -> AgentResult:
        lock = self._session_locks.setdefault(session_id, asyncio.Lock())
        self._session_lock_users[session_id] = self._session_lock_users.get(session_id, 0) + 1
        try:
            # A queued request must not consume a cross-session slot while it waits
            # behind earlier work for the same session.
            async with lock, self._session_slots:
                await self._emit(
                    AgentEventKind.RUN_STARTED, run_id, session_id, trace_id, {}
                )
                observer = _RuntimeObserver(self, run_id, session_id, trace_id)
                agent = _CoreAgent(self.spec._services(self.llm, observer))
                try:
                    result = await agent.run(request)
                except asyncio.CancelledError:
                    await self._emit(
                        AgentEventKind.RUN_CANCELLED, run_id, session_id, trace_id, {}
                    )
                    raise
                except BaseException as error:
                    await self._emit(
                        AgentEventKind.RUN_FAILED,
                        run_id,
                        session_id,
                        trace_id,
                        {"error_type": type(error).__name__, "message": str(error)},
                    )
                    raise
                await self._emit(
                    AgentEventKind.RUN_COMPLETED,
                    run_id,
                    session_id,
                    trace_id,
                    {"reply": result.reply},
                )
                return result
        finally:
            remaining = self._session_lock_users[session_id] - 1
            if remaining:
                self._session_lock_users[session_id] = remaining
            else:
                self._session_lock_users.pop(session_id, None)
                if self._session_locks.get(session_id) is lock:
                    self._session_locks.pop(session_id, None)

    async def cancel(self, run_id: str) -> bool:
        """Request cancellation of exactly one active or queued run."""
        task = self._active.get(run_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    def health(self) -> HealthReport:
        """Return aggregate readiness plus bounded runtime counters."""
        state = {
            LifecyclePhase.READY: HealthState.READY,
            LifecyclePhase.FAILED: HealthState.FAILED,
            LifecyclePhase.STOPPED: HealthState.STOPPED,
        }.get(self.phase, HealthState.STARTING)
        extension_health = self._lifecycle.health()
        if state is HealthState.READY and any(
            report.state in (HealthState.DEGRADED, HealthState.FAILED)
            for report in extension_health.values()
        ):
            state = HealthState.DEGRADED
        return HealthReport(
            state,
            metadata={
                "active_runs": len(self._active),
                "session_locks": len(self._session_locks),
                "accepting": self._accepting,
                "extensions": {
                    name: report.state.value for name, report in extension_health.items()
                },
            },
        )

    async def stop(self) -> None:
        """Drain or cancel in-flight work, then release all owned resources."""
        async with self._stop_lock:
            if self.phase is LifecyclePhase.STOPPED:
                return
            self._accepting = False
            tasks = tuple({task for task in self._active.values() if not task.done()})
            if tasks:
                _, pending = await asyncio.wait(tasks, timeout=self.spec.shutdown_timeout)
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
            deliveries = tuple(task for task in self._delivery_tasks if not task.done())
            if deliveries:
                _, pending_deliveries = await asyncio.wait(
                    deliveries, timeout=self.spec.shutdown_timeout
                )
                for delivery_task in pending_deliveries:
                    delivery_task.cancel()
                if pending_deliveries:
                    await asyncio.gather(*pending_deliveries, return_exceptions=True)
            lifecycle_error: BaseException | None = None
            try:
                await self._lifecycle.stop()
            except BaseException as error:
                lifecycle_error = error
            resource_error: BaseException | None = None
            try:
                await self._close_owned_resources()
            except BaseException as error:
                resource_error = error
            if resource_error is not None and not isinstance(resource_error, Exception):
                raise resource_error
            if lifecycle_error is not None:
                raise lifecycle_error
            if resource_error is not None:
                raise resource_error

    async def _close_owned_resources(self) -> None:
        """Attempt every owned resource cleanup before propagating failures."""
        operations: list[tuple[str, Callable[[], Any]]] = []
        if self.spec.owns_memory:
            shutdown = getattr(self.spec.memory, "shutdown", None)
            if callable(shutdown):
                operations.append(("memory.shutdown", shutdown))
            close = getattr(self.spec.memory, "close", None)
            if callable(close):
                operations.append(("memory.close", close))
        if self.spec.owns_llm:
            operations.append(("llm.stop", self._stop_llm))

        failures: list[tuple[str, Exception]] = []
        control_error: BaseException | None = None
        for name, operation in operations:
            try:
                value = operation()
                if inspect.isawaitable(value):
                    await value
            except BaseException as error:
                if isinstance(error, Exception):
                    failures.append((name, error))
                elif control_error is None:
                    control_error = error
        if control_error is not None:
            raise control_error
        if len(failures) == 1:
            raise failures[0][1]
        if failures:
            names = ", ".join(name for name, _error in failures)
            raise RuntimeError(f"failed to close owned Agent resources: {names}") from failures[0][1]

    async def _stop_llm(self) -> None:
        stop = getattr(self.llm, "stop", None)
        if callable(stop):
            await stop()
        else:
            await self.llm.close()

    def _next_event(
        self,
        kind: AgentEventKind,
        run_id: str,
        session_id: str,
        trace_id: str,
        payload: dict[str, Any],
    ) -> AgentEvent:
        sequence = self._sequences.get(run_id, 0)
        self._sequences[run_id] = sequence + 1
        return AgentEvent(kind, run_id, session_id, trace_id, sequence, payload=payload)

    async def _emit(
        self,
        kind: AgentEventKind,
        run_id: str,
        session_id: str,
        trace_id: str,
        payload: dict[str, Any],
    ) -> None:
        event = self._next_event(kind, run_id, session_id, trace_id, payload)
        for subscriber in tuple(self._subscribers.values()):
            try:
                value = subscriber(event)
                if inspect.isawaitable(value):
                    await value
            except Exception:
                _logger.exception("Agent event subscriber failed for %s", event.kind.value)

    def _emit_nowait(
        self,
        kind: AgentEventKind,
        run_id: str,
        session_id: str,
        trace_id: str,
        payload: dict[str, Any],
    ) -> None:
        event = self._next_event(kind, run_id, session_id, trace_id, payload)

        async def deliver() -> None:
            for subscriber in tuple(self._subscribers.values()):
                try:
                    value = subscriber(event)
                    if inspect.isawaitable(value):
                        await value
                except Exception:
                    _logger.exception(
                        "Agent event subscriber failed for %s", event.kind.value
                    )

        task = asyncio.create_task(deliver())
        self._delivery_tasks.add(task)
        task.add_done_callback(self._delivery_tasks.discard)


__all__ = [
    "AgentEvent",
    "AgentEventKind",
    "AgentEventSubscriber",
    "AgentObserver",
    "AgentRequest",
    "AgentResult",
    "AgentRuntime",
    "AgentSpec",
    "AgentSettings",
]
