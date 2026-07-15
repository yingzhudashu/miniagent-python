"""执行轮流式聚合、重试与可选观测路径的行为矩阵。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from miniagent.agent.execution_turn import ExecutionTurnStreamer
from miniagent.llm.legacy_transport import LLMTransportError


def _event(*, content: str | None = None, tool: object | None = None, usage: object = None):
    return SimpleNamespace(content_delta=content, tool_call_delta=tool, usage=usage)


def _tool_delta(
    index: int,
    *,
    call_id: str | None = None,
    name: str | None = None,
    arguments: str | None = None,
):
    return SimpleNamespace(index=index, id=call_id, name=name, arguments=arguments)


def _streamer(
    *,
    responses: bool = False,
    thinking: object | None = None,
    history: dict[str, list[str]] | None = None,
    debug: bool = False,
    log_file: str | None = None,
    activity: bool = False,
) -> ExecutionTurnStreamer:
    context = MagicMock()
    context.get_messages.return_value = [{"role": "user", "content": "hello"}]
    return ExecutionTurnStreamer(
        context_manager=context,
        agent_config=SimpleNamespace(
            debug=debug,
            log_file=log_file,
            log_token_usage=True,
        ),
        on_thinking=thinking,
        phase_header_sent=set(),
        model_config=SimpleNamespace(wire_api="responses" if responses else "chat_completions"),
        session_key="session-1",
        llm_client=object(),
        exec_hist_segments=history if history is not None else {},
        activity_log_enabled=activity,
        activity_log=object(),
        separator="\n---\n",
    )


@pytest.mark.asyncio
async def test_stream_exec_turn_aggregates_tools_and_records_observability() -> None:
    usage = SimpleNamespace(model_dump=lambda: {"total_tokens": 7})

    async def events(*args, **kwargs):
        del args, kwargs
        yield _event(content="answer")
        yield _event(tool=_tool_delta(1, call_id="bad", name="broken", arguments="{"))
        yield _event(tool=_tool_delta(0, call_id="call", name="read", arguments='{"p"'))
        yield _event(tool=_tool_delta(0, arguments=": 1}"), usage=usage)

    thinking = object()
    history = {"[执行]": ["earlier", " "]}
    streamer = _streamer(
        thinking=thinking,
        history=history,
        debug=True,
        log_file="trace.jsonl",
        activity=True,
    )
    thinking_calls = AsyncMock()
    log_calls: list[tuple[object, ...]] = []

    async def fake_to_thread(function, *args):
        log_calls.append((function, *args))

    traces: list[dict[str, object]] = []
    with (
        patch("miniagent.agent.execution_turn.stream_completion", side_effect=events),
        patch(
            "miniagent.agent.execution_turn.resolve_exec_completion_kwargs",
            return_value={"model": "model-x"},
        ),
        patch("miniagent.agent.execution_turn.invoke_on_thinking", thinking_calls),
        patch("miniagent.agent.execution_turn._tool_intent_in_thinking_enabled", return_value=True),
        patch("miniagent.agent.execution_turn._extract_tool_intent", return_value="读取内容"),
        patch("miniagent.agent.execution_turn.emit_trace", side_effect=traces.append),
        patch("miniagent.agent.execution_turn.asyncio.to_thread", side_effect=fake_to_thread),
        patch(
            "miniagent.agent.execution_turn.invoke_activity_log", new_callable=AsyncMock
        ) as activity,
    ):
        message, kwargs, _, actual_usage, content, label = await streamer.stream_exec_turn(
            None, ["tool-schema"], "[执行]"
        )

    assert content == "answer"
    assert label == "[执行]"
    assert kwargs["model"] == "model-x"
    assert actual_usage is usage
    assert [call.function.name for call in message.tool_calls] == ["read", "broken"]
    assert message.tool_calls[0]._args_dict == {"p": 1}
    assert message.tool_calls[1]._args_dict == {}
    assert history["[执行]"] == ["earlier", " ", "answer"]
    assert thinking_calls.await_count == 4
    assert log_calls and log_calls[0][1] == "trace.jsonl"
    activity.assert_awaited_once()
    assert all(event.get("session_key") == "session-1" for event in traces)
    assert all("self.session_key" not in event for event in traces)


@pytest.mark.asyncio
async def test_stream_exec_turn_retries_transient_responses_error_without_output() -> None:
    attempts = 0

    async def events(*args, **kwargs):
        nonlocal attempts
        del args, kwargs
        attempts += 1
        if attempts == 1:
            raise RuntimeError("gateway")
        yield _event(content="recovered")

    streamer = _streamer(responses=True)
    failure = SimpleNamespace(retryable=True, category="gateway")
    with (
        patch("miniagent.agent.execution_turn.stream_completion", side_effect=events),
        patch(
            "miniagent.agent.execution_turn.resolve_exec_completion_kwargs",
            return_value={"model": "model-x"},
        ),
        patch("miniagent.agent.execution_turn.classify_transport_error", return_value=failure),
        patch("miniagent.agent.execution_turn.emit_trace"),
    ):
        message, *_ = await streamer.stream_exec_turn(None, [], "[执行]")

    assert attempts == 2
    assert message.content == "recovered"


@pytest.mark.asyncio
async def test_stream_exec_turn_never_replays_after_partial_output() -> None:
    attempts = 0

    async def events(*args, **kwargs):
        nonlocal attempts
        del args, kwargs
        attempts += 1
        yield _event(content="partial")
        raise RuntimeError("gateway")

    streamer = _streamer(responses=True)
    failure = SimpleNamespace(retryable=True, category="gateway")
    with (
        patch("miniagent.agent.execution_turn.stream_completion", side_effect=events),
        patch(
            "miniagent.agent.execution_turn.resolve_exec_completion_kwargs",
            return_value={"model": "model-x"},
        ),
        patch("miniagent.agent.execution_turn.classify_transport_error", return_value=failure),
        patch("miniagent.agent.execution_turn.emit_trace"),
        pytest.raises(RuntimeError, match="gateway"),
    ):
        await streamer.stream_exec_turn(None, [], "[执行]")

    assert attempts == 1


@pytest.mark.asyncio
async def test_stream_exec_turn_wraps_repeated_transient_responses_error() -> None:
    async def events(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("gateway")
        yield

    streamer = _streamer(responses=True)
    failure = SimpleNamespace(retryable=True, category="gateway")
    with (
        patch("miniagent.agent.execution_turn.stream_completion", side_effect=events),
        patch(
            "miniagent.agent.execution_turn.resolve_exec_completion_kwargs",
            return_value={"model": "model-x"},
        ),
        patch("miniagent.agent.execution_turn.classify_transport_error", return_value=failure),
        patch("miniagent.agent.execution_turn.emit_trace"),
        pytest.raises(LLMTransportError, match="repeatedly rejected"),
    ):
        await streamer.stream_exec_turn(None, [], "[执行]")


@pytest.mark.asyncio
async def test_stream_exec_turn_rejects_repeated_empty_responses() -> None:
    async def events(*args, **kwargs):
        del args, kwargs
        if False:
            yield None

    streamer = _streamer(responses=True)
    with (
        patch("miniagent.agent.execution_turn.stream_completion", side_effect=events),
        patch(
            "miniagent.agent.execution_turn.resolve_exec_completion_kwargs",
            return_value={"model": "model-x"},
        ),
        patch("miniagent.agent.execution_turn.emit_trace"),
        pytest.raises(LLMTransportError, match="no text or tool calls"),
    ):
        await streamer.stream_exec_turn(None, [], "[执行]")


@pytest.mark.asyncio
async def test_thinking_failures_are_non_fatal_and_phase_can_retry_later() -> None:
    async def events(*args, **kwargs):
        del args, kwargs
        yield _event(content="ok")

    streamer = _streamer(thinking=object())
    callback = AsyncMock(side_effect=RuntimeError("display unavailable"))
    with (
        patch("miniagent.agent.execution_turn.stream_completion", side_effect=events),
        patch(
            "miniagent.agent.execution_turn.resolve_exec_completion_kwargs",
            return_value={"model": "model-x"},
        ),
        patch("miniagent.agent.execution_turn.invoke_on_thinking", callback),
        patch("miniagent.agent.execution_turn.emit_trace"),
    ):
        message, *_ = await streamer.stream_exec_turn(None, [], "[执行]")

    assert message.content == "ok"
    assert "[执行]" not in streamer._phase_header_sent
    assert callback.await_count == 2
