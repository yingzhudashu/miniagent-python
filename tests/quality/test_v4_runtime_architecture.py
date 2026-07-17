"""Behavioral contracts for the V4 four-module runtime architecture."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from miniagent.agent import (
    AgentEventKind,
    AgentRequest,
    AgentRuntime,
    AgentSpec,
    JsonlTraceExporter,
)
from miniagent.agent.lifecycle import HealthState
from miniagent.agent.rag import HybridRAGExtension, RAGDocument
from miniagent.agent.settings import AgentSettings
from miniagent.agent.types.agent import AgentRunResult
from miniagent.assistant import AssistantSpec, create_assistant
from miniagent.ui import QueueUISurface, UIInput, UIInputKind, UITarget


class FakeLLM:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class FakeMemory:
    def __init__(self) -> None:
        self.shutdown_called = False
        self.close_called = False

    async def shutdown(self) -> None:
        self.shutdown_called = True

    def close(self) -> None:
        self.close_called = True


def runtime_spec(runner, **overrides):
    values = {
        "settings": AgentSettings({}),
        "registry": MagicMock(),
        "memory": FakeMemory(),
        "knowledge": MagicMock(),
        "runner": runner,
    }
    values.update(overrides)
    return AgentSpec(**values)


@pytest.mark.asyncio
async def test_agent_runtime_serializes_each_session_and_emits_ordered_events() -> None:
    active = 0
    peak = 0

    async def runner(user_input: str, **_kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return AgentRunResult(reply=user_input.upper())

    llm = FakeLLM()
    spec = runtime_spec(runner)
    runtime = AgentRuntime(spec, llm)  # type: ignore[arg-type]
    events = []
    runtime.subscribe(events.append)
    await runtime.start()

    first = asyncio.create_task(runtime.run(AgentRequest("a", session_key="same")))
    second = asyncio.create_task(runtime.run(AgentRequest("b", session_key="same")))
    assert [result.reply for result in await asyncio.gather(first, second)] == ["A", "B"]
    assert peak == 1
    assert [event.kind for event in events] == [
        AgentEventKind.RUN_STARTED,
        AgentEventKind.RUN_COMPLETED,
        AgentEventKind.RUN_STARTED,
        AgentEventKind.RUN_COMPLETED,
    ]
    assert all(event.trace_id and event.run_id for event in events)

    await runtime.stop()
    assert llm.closed is True
    assert spec.memory.shutdown_called is True
    assert spec.memory.close_called is True


@pytest.mark.asyncio
async def test_agent_runtime_cancel_is_scoped_to_run_id() -> None:
    entered = asyncio.Event()

    async def runner(*_args, **_kwargs):
        entered.set()
        await asyncio.Event().wait()

    runtime = AgentRuntime(runtime_spec(runner), FakeLLM())  # type: ignore[arg-type]
    events = []
    runtime.subscribe(events.append)
    await runtime.start()
    task = asyncio.create_task(
        runtime.run(AgentRequest("wait", session_key="s"), run_id="run-1")
    )
    await entered.wait()
    assert await runtime.cancel("run-1") is True
    with pytest.raises(asyncio.CancelledError):
        await task
    assert AgentEventKind.RUN_CANCELLED in [event.kind for event in events]
    assert await runtime.cancel("missing") is False
    await runtime.stop()


@pytest.mark.asyncio
async def test_trace_exporter_is_runtime_scoped_and_metrics_only(tmp_path: Path) -> None:
    async def runner(user_input: str, **_kwargs):
        return AgentRunResult(reply=f"secret:{user_input}")

    output = tmp_path / "trace.jsonl"
    trace = JsonlTraceExporter(output)
    runtime = AgentRuntime(
        runtime_spec(runner), FakeLLM(), (trace,)  # type: ignore[arg-type]
    )
    await runtime.start()
    await runtime.run(AgentRequest("private", session_key="s"))
    await runtime.stop()

    rows = [json.loads(line) for line in output.read_text("utf-8").splitlines()]
    assert [row["sequence"] for row in rows] == list(range(len(rows)))
    assert "private" not in output.read_text("utf-8")
    assert trace.health().state is HealthState.STOPPED


@pytest.mark.asyncio
async def test_rag_keyword_mode_and_disabled_mode() -> None:
    rag = HybridRAGExtension(vector_enabled=False)
    await rag.initialize()
    await rag.start()
    await rag.add(RAGDocument("one", "Python agent runtime"))
    await rag.add(RAGDocument("two", "Feishu user interface"))
    results = await rag.retrieve("agent runtime")
    assert results[0].document.document_id == "one"
    await rag.stop()

    disabled = HybridRAGExtension(enabled=False, vector_enabled=False)
    await disabled.initialize()
    await disabled.start()
    assert await disabled.retrieve("anything") == ()


@dataclass
class SurfaceFactory:
    rendered: list
    instances: list

    def __call__(self):
        surface = QueueUISurface(
            "test",
            renderer=lambda event, _target: self.rendered.append(event),
        )
        self.instances.append(surface)
        return surface


@pytest.mark.asyncio
async def test_assistant_spec_routes_ui_without_channel_logic_in_agent() -> None:
    async def runner(user_input: str, **_kwargs):
        return AgentRunResult(reply=f"answer:{user_input}")

    rendered: list = []
    instances: list = []
    spec = AssistantSpec(
        name="test-assistant",
        agent_factory=lambda: AgentRuntime(
            runtime_spec(runner), FakeLLM()  # type: ignore[arg-type]
        ),
        surface_factories=(SurfaceFactory(rendered, instances),),
        state_dir="isolated",
    )
    application = create_assistant(spec)
    await application.start()
    assert instances
    run_id = await application.container.dispatch(
        UIInput(
            UIInputKind.MESSAGE,
            UITarget("test", "conversation"),
            "hello",
            session_id="session",
        )
    )
    assert run_id
    for _ in range(100):
        if rendered and rendered[-1].kind is AgentEventKind.RUN_COMPLETED:
            break
        await asyncio.sleep(0.001)
    assert rendered[-1].kind is AgentEventKind.RUN_COMPLETED
    assert rendered[-1].payload["reply"] == "answer:hello"
    assert application.health().state is HealthState.READY
    await application.stop()
