"""Behavioral contracts for the V4 four-module runtime architecture."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from miniagent.agent import (
    AgentEvent,
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
from miniagent.ui import CLISurface, QueueUISurface, UIInput, UIInputKind, UITarget


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
    assert runtime.health().metadata["session_locks"] == 0

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
async def test_same_session_queue_does_not_starve_another_session() -> None:
    """Waiting behind a session lock must not consume a cross-session slot."""
    first_entered = asyncio.Event()
    other_entered = asyncio.Event()
    release_first = asyncio.Event()

    async def runner(user_input: str, **_kwargs):
        if user_input == "first":
            first_entered.set()
            await release_first.wait()
        elif user_input == "other":
            other_entered.set()
        return AgentRunResult(reply=user_input)

    runtime = AgentRuntime(
        runtime_spec(runner, max_parallel_sessions=2), FakeLLM()  # type: ignore[arg-type]
    )
    await runtime.start()
    first = asyncio.create_task(runtime.run(AgentRequest("first", session_key="same")))
    await first_entered.wait()
    queued = asyncio.create_task(runtime.run(AgentRequest("queued", session_key="same")))
    for _ in range(100):
        if runtime._session_lock_users.get("same") == 2:
            break
        await asyncio.sleep(0)

    other = asyncio.create_task(runtime.run(AgentRequest("other", session_key="other")))
    await asyncio.wait_for(other_entered.wait(), timeout=0.2)
    release_first.set()
    assert [result.reply for result in await asyncio.gather(first, queued, other)] == [
        "first",
        "queued",
        "other",
    ]
    assert runtime.health().metadata["session_locks"] == 0
    await runtime.stop()


@pytest.mark.asyncio
async def test_queued_run_cancellation_releases_session_lock_usage() -> None:
    """Cancellation while waiting for a session lock must not leak lock state."""
    first_entered = asyncio.Event()
    release_first = asyncio.Event()

    async def runner(user_input: str, **_kwargs):
        if user_input == "first":
            first_entered.set()
            await release_first.wait()
        return AgentRunResult(reply=user_input)

    runtime = AgentRuntime(runtime_spec(runner), FakeLLM())  # type: ignore[arg-type]
    await runtime.start()
    first = asyncio.create_task(
        runtime.run(AgentRequest("first", session_key="same"), run_id="first")
    )
    await first_entered.wait()
    queued = asyncio.create_task(
        runtime.run(AgentRequest("queued", session_key="same"), run_id="queued")
    )
    for _ in range(100):
        if runtime._session_lock_users.get("same") == 2:
            break
        await asyncio.sleep(0)
    assert await runtime.cancel("queued") is True
    with pytest.raises(asyncio.CancelledError):
        await queued
    assert runtime.health().metadata["session_locks"] == 1
    release_first.set()
    await first
    assert runtime.health().metadata["session_locks"] == 0
    await runtime.stop()


@pytest.mark.asyncio
async def test_runtime_bounds_non_terminal_event_delivery_during_shutdown() -> None:
    """A blocked fire-and-forget subscriber cannot hang runtime shutdown."""
    delivery_entered = asyncio.Event()

    async def runner(user_input: str, **kwargs):
        kwargs["on_tool_call"]("tool", "{}", "ok")
        return AgentRunResult(reply=user_input)

    async def subscriber(event: AgentEvent) -> None:
        if event.kind is AgentEventKind.TOOL_STARTED:
            delivery_entered.set()
            await asyncio.Event().wait()

    runtime = AgentRuntime(
        runtime_spec(runner, shutdown_timeout=0.01), FakeLLM()  # type: ignore[arg-type]
    )
    runtime.subscribe(subscriber)
    await runtime.start()
    await runtime.run(AgentRequest("done", session_key="session"))
    await delivery_entered.wait()
    await asyncio.wait_for(runtime.stop(), timeout=0.2)
    assert not runtime._delivery_tasks


@pytest.mark.asyncio
async def test_runtime_attempts_all_owned_resource_cleanup_after_failure() -> None:
    """One resource failure must not skip later memory and LLM cleanup."""

    class FailingMemory(FakeMemory):
        async def shutdown(self) -> None:
            self.shutdown_called = True
            raise OSError("flush failed")

    async def runner(user_input: str, **_kwargs):
        return AgentRunResult(reply=user_input)

    memory = FailingMemory()
    llm = FakeLLM()
    runtime = AgentRuntime(runtime_spec(runner, memory=memory), llm)  # type: ignore[arg-type]
    await runtime.start()
    with pytest.raises(OSError, match="flush failed"):
        await runtime.stop()
    assert memory.close_called is True
    assert llm.closed is True


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


@pytest.mark.asyncio
async def test_rag_replacement_discards_a_stale_vector_when_refresh_fails() -> None:
    """Fail-open replacement must never rank new text using the old text's vector."""

    class Embeddings:
        async def embed(self, text: str):
            if text == "replacement":
                raise OSError("offline")
            return [1.0, 0.0]

        async def close(self):
            return None

    rag = HybridRAGExtension(embedding_client=Embeddings())  # type: ignore[arg-type]
    await rag.initialize()
    await rag.start()
    await rag.add(RAGDocument("one", "original"))
    assert "one" in rag._vectors
    await rag.add(RAGDocument("one", "replacement"))
    assert "one" not in rag._vectors
    await rag.stop()


@pytest.mark.asyncio
async def test_rag_vector_ranking_persistence_and_removal(tmp_path: Path) -> None:
    """RAG state round-trips immutable metadata, vectors, ranking, and removal."""

    class Embeddings:
        def __init__(self) -> None:
            self.closed = False

        async def embed(self, text: str):
            return [0.0, 1.0] if "second" in text else [1.0, 0.0]

        async def close(self):
            self.closed = True

    state_path = tmp_path / "rag" / "state.json"
    embeddings = Embeddings()
    document = RAGDocument("one", "first agent", {"source": "test"})
    with pytest.raises(TypeError):
        document.metadata["source"] = "changed"  # type: ignore[index]
    rag = HybridRAGExtension(
        embedding_client=embeddings, state_path=state_path, min_score=0.1
    )  # type: ignore[arg-type]
    await rag.initialize()
    await rag.start()
    await rag.add(document)
    await rag.add(RAGDocument("two", "second topic"))
    ranked = await rag.retrieve("first", top_k=1)
    assert ranked[0].document.document_id == "one"
    assert ranked[0].vector_score == 1.0
    assert rag.search("agent", top_k=1, max_chars=5) == "first"
    assert rag.health().metadata == {"documents": 2, "vectors": 2}
    assert rag.remove("missing") is False
    assert rag.remove("two") is True
    await rag.stop()
    assert embeddings.closed is True
    assert state_path.is_file()

    restored_embeddings = Embeddings()
    restored = HybridRAGExtension(
        embedding_client=restored_embeddings, state_path=state_path
    )  # type: ignore[arg-type]
    await restored.initialize()
    await restored.start()
    assert restored.health().metadata == {"documents": 1, "vectors": 1}
    assert (await restored.retrieve("first"))[0].document.metadata["source"] == "test"
    await restored.stop()


@pytest.mark.asyncio
async def test_rag_validation_fail_closed_and_atomic_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invalid policy and persistence failures are explicit and leave no temp file."""
    with pytest.raises(ValueError, match="top_k"):
        HybridRAGExtension(top_k=0)
    with pytest.raises(ValueError, match="document"):
        RAGDocument("", "text")
    with pytest.raises(ValueError, match="EmbeddingClient"):
        await HybridRAGExtension().initialize()

    class FailingEmbeddings:
        async def embed(self, _text: str):
            raise OSError("offline")

        async def close(self):
            return None

    rag = HybridRAGExtension(
        embedding_client=FailingEmbeddings(), fail_open=False  # type: ignore[arg-type]
    )
    await rag.initialize()
    await rag.start()
    with pytest.raises(OSError, match="offline"):
        await rag.add(RAGDocument("one", "text"))
    with pytest.raises(OSError, match="offline"):
        await rag.retrieve("text")
    assert rag.health().state is HealthState.DEGRADED
    assert HybridRAGExtension._cosine([], []) == 0.0
    assert HybridRAGExtension._cosine([1.0], [1.0, 2.0]) == 0.0
    assert HybridRAGExtension._cosine([0.0], [1.0]) == 0.0

    state_path = tmp_path / "state.json"
    persistent = HybridRAGExtension(
        vector_enabled=False, state_path=state_path
    )
    await persistent.initialize()
    await persistent.start()
    await persistent.add(RAGDocument("one", "text"))
    monkeypatch.setattr("miniagent.agent.rag.os.replace", MagicMock(side_effect=OSError("disk")))
    with pytest.raises(OSError, match="disk"):
        await persistent.stop()
    assert not list(tmp_path.glob(".*.tmp"))


@pytest.mark.asyncio
async def test_cli_surface_rejects_events_for_another_surface() -> None:
    """The fallback renderer enforces the same routing boundary as custom renderers."""
    surface = CLISurface(output=lambda _text: None)
    with pytest.raises(ValueError, match="target"):
        await surface.render(
            AgentEvent(
                AgentEventKind.RUN_COMPLETED,
                "run",
                "session",
                "trace",
                0,
                payload={"reply": "ok"},
            ),
            UITarget("feishu", "chat"),
        )


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


class CountingSurface(QueueUISurface):
    """Queue surface exposing lifecycle call counts for concurrency tests."""

    def __init__(self) -> None:
        super().__init__("counting")
        self.initialize_calls = 0
        self.start_calls = 0
        self.stop_calls = 0

    async def initialize(self) -> None:
        self.initialize_calls += 1
        await super().initialize()

    async def start(self) -> None:
        self.start_calls += 1
        await super().start()

    async def stop(self) -> None:
        self.stop_calls += 1
        await super().stop()


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
    target = UITarget("test", "conversation")
    with pytest.raises(RuntimeError, match="not accepting"):
        await application.container.dispatch(
            UIInput(UIInputKind.MESSAGE, target, "too early", session_id="session")
        )
    await application.start()
    assert instances
    with pytest.raises(RuntimeError, match="command handler"):
        await application.container.dispatch(
            UIInput(UIInputKind.COMMAND, target, "/help", session_id="session")
        )
    with pytest.raises(RuntimeError, match="confirmation handler"):
        await application.container.dispatch(
            UIInput(UIInputKind.CONFIRMATION, target, "confirm", session_id="session")
        )
    run_id = await application.container.dispatch(
        UIInput(
            UIInputKind.MESSAGE,
            target,
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


@pytest.mark.asyncio
async def test_composed_runtime_serializes_concurrent_start_and_stop() -> None:
    """Concurrent lifecycle callers must observe one complete transition."""

    async def runner(user_input: str, **_kwargs):
        return AgentRunResult(reply=user_input)

    surface = CountingSurface()
    application = create_assistant(
        AssistantSpec(
            name="lifecycle",
            agent_factory=lambda: AgentRuntime(
                runtime_spec(runner), FakeLLM()  # type: ignore[arg-type]
            ),
            surface_factories=(lambda: surface,),
        )
    )
    await asyncio.gather(application.start(), application.start())
    assert (surface.initialize_calls, surface.start_calls) == (1, 1)
    assert application.health().metadata["accepting"] is True
    await asyncio.gather(application.stop(), application.stop())
    assert surface.stop_calls == 1
    assert application.health().metadata["active_runs"] == 0
    assert application.health().metadata["targets"] == 0
    with pytest.raises(RuntimeError, match="cannot be restarted"):
        await application.start()


@pytest.mark.asyncio
async def test_composed_runtime_keeps_run_targets_and_cancels_oldest_session_run() -> None:
    """Shared sessions must not overwrite event routing or current-run cancellation."""
    first_entered = asyncio.Event()
    rendered: dict[str, list] = {"one": [], "two": []}

    async def runner(user_input: str, **_kwargs):
        if user_input == "first":
            first_entered.set()
            await asyncio.Event().wait()
        return AgentRunResult(reply=user_input)

    def surface_factory(surface_id: str):
        def create():
            return QueueUISurface(
                surface_id,
                renderer=lambda event, _target: rendered[surface_id].append(event),
            )

        return create

    application = create_assistant(
        AssistantSpec(
            name="routing",
            agent_factory=lambda: AgentRuntime(
                runtime_spec(runner), FakeLLM()  # type: ignore[arg-type]
            ),
            surface_factories=(surface_factory("one"), surface_factory("two")),
        )
    )
    await application.start()
    first = await application.container.dispatch(
        UIInput(
            UIInputKind.MESSAGE,
            UITarget("one", "conversation-1"),
            "first",
            session_id="shared",
        )
    )
    await first_entered.wait()
    second = await application.container.dispatch(
        UIInput(
            UIInputKind.MESSAGE,
            UITarget("two", "conversation-2"),
            "second",
            session_id="shared",
        )
    )

    cancelled = await application.container.dispatch(
        UIInput(
            UIInputKind.CANCEL,
            UITarget("two", "conversation-2"),
            session_id="shared",
        )
    )
    assert cancelled == first

    for _ in range(100):
        second_completed = any(
            event.run_id == second and event.kind is AgentEventKind.RUN_COMPLETED
            for event in rendered["two"]
        )
        if second_completed and application.health().metadata["active_runs"] == 0:
            break
        await asyncio.sleep(0.001)
    assert all(event.run_id == first for event in rendered["one"])
    assert all(event.run_id == second for event in rendered["two"])
    assert AgentEventKind.RUN_CANCELLED in {event.kind for event in rendered["one"]}
    assert AgentEventKind.RUN_COMPLETED in {event.kind for event in rendered["two"]}
    assert application.health().metadata["active_runs"] == 0
    assert application.health().metadata["targets"] == 0
    await application.stop()
