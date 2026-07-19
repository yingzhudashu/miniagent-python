"""Failure and boundary contracts for the V4 runtime composition layers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pytest

from miniagent.agent import AgentEvent, AgentEventKind, AgentRequest, AgentRuntime, AgentSpec
from miniagent.agent.lifecycle import HealthReport, HealthState
from miniagent.agent.settings import AgentSettings
from miniagent.agent.types.agent import AgentRunResult
from miniagent.agent.types.confirmation import ConfirmationResult
from miniagent.agent.types.planning import StructuredPlan
from miniagent.assistant.app import AssistantApplication, create_assistant, run_assistant
from miniagent.assistant.composition import ComposedAssistantRuntime
from miniagent.assistant.spec import AssistantSpec
from miniagent.llm.gateway import LLMGateway
from miniagent.ui import CLISurface, QueueUISurface, UIInput, UIInputKind, UITarget


class FakeLLM:
    """Minimal owned gateway recording lifecycle calls."""

    def __init__(self) -> None:
        self.initialized = False
        self.started = False
        self.stopped = False

    async def initialize(self) -> None:
        self.initialized = True

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


class FakeMemory:
    """Minimal owned memory runtime recording both cleanup protocols."""

    def __init__(self) -> None:
        self.shutdown_called = False
        self.close_called = False

    async def shutdown(self) -> None:
        self.shutdown_called = True

    def close(self) -> None:
        self.close_called = True


def runtime_spec(runner: Any, **overrides: Any) -> AgentSpec:
    values: dict[str, Any] = {
        "settings": AgentSettings({}),
        "registry": MagicMock(),
        "memory": FakeMemory(),
        "knowledge": MagicMock(),
        "runner": runner,
    }
    values.update(overrides)
    return AgentSpec(**values)


def event(kind: AgentEventKind = AgentEventKind.RUN_COMPLETED) -> AgentEvent:
    return AgentEvent(kind, "run", "session", "trace", 0, payload={"reply": "done"})


@pytest.mark.asyncio
async def test_queue_surface_validates_lifecycle_routing_and_backpressure() -> None:
    """A full queue still receives a stop sentinel and never leaks stale input."""
    with pytest.raises(ValueError, match="surface_id"):
        QueueUISurface(" ")
    with pytest.raises(ValueError, match="queue_size"):
        QueueUISurface("test", queue_size=0)

    surface = QueueUISurface("test", queue_size=1)
    target = UITarget("test", "conversation")
    input_ = UIInput(UIInputKind.MESSAGE, target, "hello")
    assert surface.health().state is HealthState.STOPPED
    with pytest.raises(RuntimeError, match="not ready"):
        await surface.publish(input_)

    await surface.initialize()
    assert surface.health().state is HealthState.STARTING
    await surface.start()
    with pytest.raises(ValueError, match="target"):
        await surface.publish(
            UIInput(UIInputKind.MESSAGE, UITarget("other", "conversation"), "hello")
        )
    await surface.publish(input_)
    assert surface.health().metadata["queued_inputs"] == 1
    await surface.stop()
    await surface.stop()
    iterator = surface.inputs()
    with pytest.raises(StopAsyncIteration):
        await anext(iterator)


@pytest.mark.asyncio
async def test_surfaces_render_sync_async_and_cli_fallback() -> None:
    calls: list[str] = []

    async def async_renderer(_event: AgentEvent, target: UITarget) -> None:
        calls.append(target.conversation_id)

    async_surface = QueueUISurface("async", renderer=async_renderer)
    await async_surface.render(event(), UITarget("async", "one"))
    sync_surface = QueueUISurface(
        "sync", renderer=lambda _event, target: calls.append(target.conversation_id)
    )
    await sync_surface.render(event(), UITarget("sync", "two"))
    with pytest.raises(ValueError, match="target"):
        await sync_surface.render(event(), UITarget("other", "two"))

    output: list[str] = []
    cli = CLISurface(output=output.append)
    await cli.render(event(), UITarget("cli", "terminal"))
    await cli.render(
        AgentEvent(AgentEventKind.RUN_STARTED, "run", "session", "trace", 0),
        UITarget("cli", "terminal"),
    )
    custom_cli = CLISurface(
        renderer=lambda _event, target: calls.append(target.conversation_id)
    )
    await custom_cli.render(event(), UITarget("cli", "three"))
    assert calls == ["one", "two", "three"]
    assert output == ["done"]
    assert async_surface.health().metadata["rendered"] == 1


@pytest.mark.asyncio
async def test_agent_runtime_observer_events_and_failure_isolation() -> None:
    """Observer callbacks and subscriber failures cannot corrupt the event stream."""
    delegated: list[str] = []

    class Observer:
        async def on_thinking(self, text: str, *_args: Any, **_kwargs: Any) -> None:
            delegated.append(f"thinking:{text}")

        def on_tool_call(self, name: str, *_args: Any) -> None:
            delegated.append(f"call:{name}")

        async def on_tool_finish(self, name: str, *_args: Any, **_kwargs: Any) -> None:
            delegated.append(f"finish:{name}")

        async def on_plan(self, plan: StructuredPlan) -> ConfirmationResult:
            delegated.append(f"plan:{plan.summary}")
            return ConfirmationResult.confirm()

        def on_reflection(self, reflection: Any) -> None:
            delegated.append(f"reflection:{reflection}")

    async def runner(user_input: str, **callbacks: Any) -> AgentRunResult:
        await callbacks["on_thinking"]("step", True, "header", reset=True)
        callbacks["on_tool_call"]("search", "{}", "pending")
        await callbacks["on_tool_finish"]("search", "{}", "ok", True)
        confirmed = await callbacks["on_plan"](
            StructuredPlan(summary="answer", steps=[], required_toolboxes=[])
        )
        assert confirmed.approved is True
        await callbacks["on_reflection"]("clear")
        if user_input == "fail":
            raise SystemExit("stop")
        return AgentRunResult(reply="ok")

    runtime = AgentRuntime(
        runtime_spec(runner, observer=Observer()), FakeLLM()  # type: ignore[arg-type]
    )
    captured: list[AgentEvent] = []

    def broken_subscriber(_event: AgentEvent) -> None:
        raise ValueError("observer offline")

    unsubscribe = runtime.subscribe(broken_subscriber)
    runtime.subscribe(captured.append)
    await runtime.start()
    assert runtime.active_run_ids == ()
    assert (await runtime.run(AgentRequest("ok"), run_id="observed")).reply == "ok"
    unsubscribe()
    await asyncio.sleep(0)
    assert delegated == [
        "thinking:step",
        "call:search",
        "finish:search",
        "plan:answer",
        "reflection:clear",
    ]
    assert {
        AgentEventKind.THINKING_DELTA,
        AgentEventKind.TOOL_STARTED,
        AgentEventKind.TOOL_FINISHED,
        AgentEventKind.CONFIRMATION_REQUIRED,
        AgentEventKind.REFLECTION,
    }.issubset({item.kind for item in captured})
    with pytest.raises(SystemExit, match="stop"):
        await runtime.run(AgentRequest("fail"), run_id="failed")
    assert any(item.kind is AgentEventKind.RUN_FAILED for item in captured)
    await runtime.stop()


@pytest.mark.asyncio
async def test_agent_runtime_event_iterator_duplicate_run_and_shutdown_cancel() -> None:
    entered = asyncio.Event()

    async def runner(_user_input: str, **_kwargs: Any) -> AgentRunResult:
        entered.set()
        await asyncio.Event().wait()
        return AgentRunResult(reply="unreachable")

    runtime = AgentRuntime(
        runtime_spec(runner, shutdown_timeout=0), FakeLLM()  # type: ignore[arg-type]
    )
    with pytest.raises(RuntimeError, match="not ready"):
        await runtime.run(AgentRequest("early"))
    await runtime.start()
    iterator = runtime.events()
    next_event = asyncio.create_task(anext(iterator))
    running = asyncio.create_task(runtime.run(AgentRequest("wait"), run_id="same"))
    assert (await next_event).kind is AgentEventKind.RUN_STARTED
    await entered.wait()
    assert runtime.active_run_ids == ("same",)
    with pytest.raises(ValueError, match="already active"):
        await runtime.run(AgentRequest("duplicate"), run_id="same")
    await iterator.aclose()
    await runtime.stop()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert runtime.health().metadata["accepting"] is False


@pytest.mark.asyncio
async def test_agent_runtime_validation_and_multi_resource_cleanup() -> None:
    async def runner(_user_input: str, **_kwargs: Any) -> AgentRunResult:
        return AgentRunResult(reply="ok")

    with pytest.raises(ValueError, match="max_parallel_sessions"):
        runtime_spec(runner, max_parallel_sessions=0)
    with pytest.raises(ValueError, match="shutdown_timeout"):
        runtime_spec(runner, shutdown_timeout=-1)

    class FailingMemory(FakeMemory):
        async def shutdown(self) -> None:
            raise OSError("shutdown")

        def close(self) -> None:
            raise ValueError("close")

    class FailingLLM(FakeLLM):
        async def stop(self) -> None:
            raise RuntimeError("gateway")

    runtime = AgentRuntime(
        runtime_spec(runner, memory=FailingMemory()), FailingLLM()  # type: ignore[arg-type]
    )
    await runtime.start()
    with pytest.raises(RuntimeError, match="memory.shutdown, memory.close, llm.stop"):
        await runtime.stop()
    await runtime.stop()


@dataclass
class LifecycleSurface(QueueUISurface):
    """Controllable surface used for composition rollback and health tests."""

    surface_id_value: str
    fail_initialize: bool = False
    fail_render: bool = False
    reported_state: HealthState = HealthState.STOPPED
    calls: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        QueueUISurface.__init__(self, self.surface_id_value)

    async def initialize(self) -> None:
        self.calls.append("initialize")
        if self.fail_initialize:
            raise ValueError("surface init")
        await super().initialize()

    async def start(self) -> None:
        self.calls.append("start")
        await super().start()

    async def stop(self) -> None:
        self.calls.append("stop")
        await super().stop()

    async def render(self, value: AgentEvent, target: UITarget) -> None:
        if self.fail_render:
            raise ValueError("render")
        await super().render(value, target)

    def health(self) -> HealthReport:
        if self.reported_state is not HealthState.STOPPED:
            return HealthReport(self.reported_state)
        return super().health()


class OneShotSurface(QueueUISurface):
    """Surface whose finite input stream lets ``serve`` finish deterministically."""

    def __init__(self, input_: UIInput) -> None:
        super().__init__(input_.target.surface_id)
        self._input = input_

    async def inputs(self):
        yield self._input


def assistant_spec(runner: Any, *surfaces: QueueUISurface, **overrides: Any) -> AssistantSpec:
    values: dict[str, Any] = {
        "name": "edge",
        "agent_factory": lambda: AgentRuntime(
            runtime_spec(runner), FakeLLM()  # type: ignore[arg-type]
        ),
        "surface_factories": tuple((lambda surface=surface: surface) for surface in surfaces),
    }
    values.update(overrides)
    return AssistantSpec(**values)


def test_composed_runtime_rejects_missing_agent_and_duplicate_surface_ids() -> None:
    container_spec = AssistantSpec(name="container", container_factory=object)
    with pytest.raises(ValueError, match="agent_factory"):
        ComposedAssistantRuntime(container_spec)

    async def runner(_user_input: str, **_kwargs: Any) -> AgentRunResult:
        return AgentRunResult(reply="ok")

    with pytest.raises(ValueError, match="unique"):
        ComposedAssistantRuntime(
            assistant_spec(runner, QueueUISurface("same"), QueueUISurface("same"))
        )


@pytest.mark.asyncio
async def test_composed_runtime_handles_commands_failures_and_unknown_targets() -> None:
    handled: list[UIInputKind] = []

    async def handler(input_: UIInput, _agent: AgentRuntime) -> None:
        handled.append(input_.kind)

    async def runner(user_input: str, **_kwargs: Any) -> AgentRunResult:
        if user_input == "fail":
            raise ValueError("run failed")
        return AgentRunResult(reply=user_input)

    surface = LifecycleSurface("known", fail_render=True)
    runtime = ComposedAssistantRuntime(
        assistant_spec(runner, surface, command_handler=handler)
    )
    await runtime.start()
    target = UITarget("known", "conversation")
    assert await runtime.dispatch(UIInput(UIInputKind.CANCEL, target)) is None
    await runtime.dispatch(UIInput(UIInputKind.COMMAND, target, "/help"))
    await runtime.dispatch(UIInput(UIInputKind.CONFIRMATION, target, "yes"))
    failed_run = await runtime.dispatch(UIInput(UIInputKind.MESSAGE, target, "fail"))
    unknown_run = await runtime.dispatch(
        UIInput(UIInputKind.MESSAGE, UITarget("missing", "conversation"), "ok")
    )
    for _ in range(100):
        if runtime.health().metadata["active_runs"] == 0:
            break
        await asyncio.sleep(0)
    assert failed_run not in runtime._targets
    assert unknown_run not in runtime._targets
    assert handled == [UIInputKind.COMMAND, UIInputKind.CONFIRMATION]
    await runtime.stop()


@pytest.mark.asyncio
async def test_composed_runtime_rolls_back_failed_surface_startup() -> None:
    async def runner(_user_input: str, **_kwargs: Any) -> AgentRunResult:
        return AgentRunResult(reply="ok")

    surface = LifecycleSurface("broken", fail_initialize=True)
    runtime = ComposedAssistantRuntime(assistant_spec(runner, surface))
    with pytest.raises(Exception, match="surface init"):
        await runtime.start()
    assert surface.calls == ["initialize", "stop"]
    assert runtime.health().metadata["accepting"] is False
    await runtime.stop()
    with pytest.raises(RuntimeError, match="cannot be restarted"):
        await runtime.start()


@pytest.mark.asyncio
async def test_composed_runtime_serve_consumes_surfaces_and_stops() -> None:
    handled: list[str] = []
    target = UITarget("oneshot", "conversation")
    surface = OneShotSurface(UIInput(UIInputKind.COMMAND, target, "/status"))

    async def runner(_user_input: str, **_kwargs: Any) -> AgentRunResult:
        return AgentRunResult(reply="ok")

    async def handler(input_: UIInput, _agent: AgentRuntime) -> None:
        handled.append(input_.content)

    runtime = ComposedAssistantRuntime(
        assistant_spec(runner, surface, command_handler=handler)
    )
    await runtime.serve()
    assert handled == ["/status"]
    assert runtime._input_tasks == set()
    assert runtime.health().state is HealthState.STOPPED


def test_assistant_application_delegates_public_entry_points(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Container:
        def __init__(self) -> None:
            self.served = False

        async def serve(self) -> None:
            self.served = True

        def health(self) -> str:
            return "ready"

    container = Container()
    application = AssistantApplication(container)  # type: ignore[arg-type]
    application.run()
    assert container.served is True
    assert application.health() == "ready"

    bare = AssistantApplication(object())  # type: ignore[arg-type]
    assert bare.health() is None
    asyncio.run(bare.stop())
    with pytest.raises(RuntimeError, match="started with run"):
        asyncio.run(bare.start())

    created = create_assistant(AssistantSpec(name="factory", container_factory=lambda: container))
    assert created.container is container
    called: list[list[str] | None] = []
    monkeypatch.setattr(
        "miniagent.assistant.runner.run_cli_boundary", lambda argv: called.append(argv)
    )
    run_assistant(["--help"])
    assert called == [["--help"]]


@pytest.mark.asyncio
async def test_gateway_lifecycle_reports_ready_and_closed() -> None:
    """The gateway lifecycle aliases expose a coherent health snapshot."""
    from tests.llm.test_llm_gateway import _gateway

    gateway, provider = _gateway()
    assert isinstance(gateway, LLMGateway)
    await gateway.initialize()
    assert gateway.health()["ready"] is False
    await gateway.start()
    health = gateway.health()
    assert health["ready"] is True
    assert health["providers"] == ("faux",)
    assert "primary" in health["models"]
    await gateway.stop()
    assert gateway.health()["closed"] is True
    assert provider.closed is True
