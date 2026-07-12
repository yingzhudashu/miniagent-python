"""execute_plan 集成测试（mock OpenAI 流式客户端）。"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from miniagent.core.executor import execute_plan
from miniagent.types.config import ModelConfig
from miniagent.types.planning import PlanStep, StructuredPlan
from tests.memory_helpers import make_knowledge_registry, make_memory_runtime
from tests.mock_strategies import (
    agent_config_with_session,
    empty_plan,
    make_ping_tool_registry,
    mock_memory_bundle,
    mock_streaming_client,
)


@contextmanager
def _responses_execution_config() -> Iterator[None]:
    config = ModelConfig(
        model="response-model",
        wire_api="responses",
        thinking_level="heavy",
        max_tokens=4096,
    )
    with (
        patch("miniagent.core.executor.get_default_model_config", return_value=config),
        patch("miniagent.core.llm_params.get_default_model_config", return_value=config),
        patch("miniagent.core.llm_transport._wire_api", return_value="responses"),
    ):
        yield


@pytest.mark.asyncio
async def test_execute_plan_uses_session_registry_for_tools() -> None:
    main, sess = make_ping_tool_registry()
    mock_client = mock_streaming_client()
    ms, al, ki = mock_memory_bundle()
    out = await execute_plan(
        empty_plan(),
        "hi",
        main,
        MagicMock(),
        agent_config_with_session(sess),
        client=mock_client,
        memory=make_memory_runtime(store=ms, activity_log=al, keyword_index=ki),
        knowledge_registry=make_knowledge_registry(),
    )
    assert "done" in out


@pytest.mark.asyncio
async def test_execute_plan_calls_on_tool_finish() -> None:
    main, sess = make_ping_tool_registry()
    mock_client = mock_streaming_client(tool_args='{"k":1}')
    ms, al, ki = mock_memory_bundle()
    finishes: list[tuple[str, str, str, bool, str]] = []

    async def on_finish(
        name: str,
        args_json: str,
        result: str,
        success: bool,
        *,
        thinking_header: str = "",
    ) -> None:
        finishes.append((name, args_json, result, success, thinking_header))

    out = await execute_plan(
        empty_plan(),
        "hi",
        main,
        MagicMock(),
        agent_config_with_session(sess),
        client=mock_client,
        memory=make_memory_runtime(store=ms, activity_log=al, keyword_index=ki),
        knowledge_registry=make_knowledge_registry(),
        on_tool_finish=on_finish,
    )
    assert "done" in out
    assert len(finishes) == 1
    assert finishes[0][0] == "ping_tool"
    assert finishes[0][3] is True
    assert finishes[0][4] == "[执行]"


@pytest.mark.asyncio
async def test_execute_plan_responses_tool_round_trip() -> None:
    main, sess = make_ping_tool_registry()
    calls: list[dict[str, Any]] = []

    async def tool_stream():
        yield SimpleNamespace(
            type="response.output_item.added",
            output_index=0,
            item=SimpleNamespace(
                type="function_call",
                id="item-1",
                call_id="call-1",
                name="ping_tool",
            ),
        )
        yield SimpleNamespace(
            type="response.function_call_arguments.delta",
            output_index=0,
            item_id="item-1",
            delta="{}",
        )
        yield SimpleNamespace(
            type="response.output_item.done",
            output_index=0,
            item=SimpleNamespace(
                type="function_call",
                call_id="call-1",
                name="ping_tool",
                arguments="{}",
            ),
        )
        yield SimpleNamespace(
            type="response.completed", response=SimpleNamespace(usage=None)
        )

    async def final_stream():
        yield SimpleNamespace(
            type="response.output_text.done",
            output_index=0,
            content_index=0,
            text="done",
        )
        yield SimpleNamespace(
            type="response.completed", response=SimpleNamespace(usage=None)
        )

    async def create_response(**kwargs: Any):
        calls.append(kwargs)
        return tool_stream() if len(calls) == 1 else final_stream()

    client = MagicMock()
    client.responses.create = AsyncMock(side_effect=create_response)
    ms, al, ki = mock_memory_bundle()
    with patch("miniagent.core.llm_transport._wire_api", return_value="responses"):
        out = await execute_plan(
            empty_plan(),
            "hi",
            main,
            MagicMock(),
            agent_config_with_session(sess),
            client=client,
            memory=make_memory_runtime(store=ms, activity_log=al, keyword_index=ki),
            knowledge_registry=make_knowledge_registry(),
        )

    assert out == "done"
    assert len(calls) == 2
    assert any(item.get("type") == "function_call" for item in calls[1]["input"])
    assert any(
        item.get("type") == "function_call_output" for item in calls[1]["input"]
    )


@pytest.mark.asyncio
async def test_execute_plan_responses_retries_transient_400_before_output() -> None:
    class GatewayInvalidRequest(Exception):
        status_code = 400

    async def final_stream():
        yield SimpleNamespace(
            type="response.output_text.done",
            output_index=0,
            content_index=0,
            text="done",
        )
        yield SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(usage=None),
        )

    main, sess = make_ping_tool_registry()
    client = MagicMock()
    client.responses.create = AsyncMock(
        side_effect=[
            GatewayInvalidRequest("invalid_request_error cch_session_id: probe"),
            GatewayInvalidRequest("invalid_request_error cch_session_id: probe"),
            final_stream(),
        ]
    )
    ms, al, ki = mock_memory_bundle()

    with _responses_execution_config():
        out = await execute_plan(
            empty_plan(),
            "hi",
            main,
            MagicMock(),
            agent_config_with_session(sess),
            client=client,
            memory=make_memory_runtime(store=ms, activity_log=al, keyword_index=ki),
            knowledge_registry=make_knowledge_registry(),
        )

    assert out == "done"
    calls = client.responses.create.await_args_list
    assert calls[0].kwargs["temperature"] == 0.7
    assert calls[0].kwargs["top_p"] == 1.0
    assert "temperature" not in calls[1].kwargs
    assert "top_p" not in calls[1].kwargs
    assert calls[2].kwargs["reasoning"] == {"effort": "medium"}


@pytest.mark.asyncio
async def test_execute_plan_responses_retries_completed_empty_stream() -> None:
    trace_events: list[dict[str, Any]] = []
    async def empty_stream():
        yield SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(usage=None),
        )

    async def final_stream():
        yield SimpleNamespace(
            type="response.output_text.done",
            output_index=0,
            content_index=0,
            text="recovered",
        )
        yield SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(usage=None),
        )

    main, sess = make_ping_tool_registry()
    client = MagicMock()
    client.responses.create = AsyncMock(
        side_effect=[empty_stream(), final_stream()]
    )
    ms, al, ki = mock_memory_bundle()

    with (
        _responses_execution_config(),
        patch("miniagent.core.executor.emit_trace", side_effect=trace_events.append),
    ):
        out = await execute_plan(
            empty_plan(),
            "hi",
            main,
            MagicMock(),
            agent_config_with_session(sess),
            client=client,
            memory=make_memory_runtime(store=ms, activity_log=al, keyword_index=ki),
            knowledge_registry=make_knowledge_registry(),
        )

    assert out == "recovered"
    assert client.responses.create.await_count == 2
    response_events = [
        event for event in trace_events if event.get("type") == "llm.response"
    ]
    assert [event.get("failure_category") for event in response_events] == [
        "empty_response",
        None,
    ]
    assert all(
        isinstance(event.get("duration_ms"), int)
        and event["duration_ms"] >= 0
        for event in response_events
    )


@pytest.mark.asyncio
async def test_execute_plan_does_not_retry_after_partial_stream_output() -> None:
    class GatewayInvalidRequest(Exception):
        status_code = 400

    async def partial_stream():
        yield SimpleNamespace(
            type="response.output_text.delta",
            output_index=0,
            content_index=0,
            delta="partial",
        )
        raise GatewayInvalidRequest("invalid_request_error cch_session_id: probe")

    main, sess = make_ping_tool_registry()
    client = MagicMock()
    client.responses.create = AsyncMock(return_value=partial_stream())
    ms, al, ki = mock_memory_bundle()

    with _responses_execution_config(), pytest.raises(GatewayInvalidRequest):
        await execute_plan(
            empty_plan(),
            "hi",
            main,
            MagicMock(),
            agent_config_with_session(sess),
            client=client,
            memory=make_memory_runtime(store=ms, activity_log=al, keyword_index=ki),
            knowledge_registry=make_knowledge_registry(),
        )

    assert client.responses.create.await_count == 1


@pytest.mark.asyncio
async def test_execute_plan_phased_last_step_grace_synthesis() -> None:
    main, sess = make_ping_tool_registry()
    plan = StructuredPlan(
        summary="s",
        steps=[PlanStep(step_number=1, description="一步", expected_input="", expected_output="")],
        required_toolboxes=[],
    )
    mock_client = mock_streaming_client(final_text="wrapped_up")
    create_kwargs: list[dict] = []
    orig = mock_client.chat.completions.create

    async def capture_kwargs(*args, **kwargs):
        create_kwargs.append(kwargs)
        return await orig(*args, **kwargs)

    mock_client.chat.completions.create = AsyncMock(side_effect=capture_kwargs)
    ms, al, ki = mock_memory_bundle()
    with (
        patch("miniagent.core.executor._env_phased_execution_enabled", return_value=True),
        patch("miniagent.core.executor.EXECUTION_STEP_MAX_TURNS", 1),
    ):
        out = await execute_plan(
            plan,
            "hi",
            main,
            MagicMock(),
            agent_config_with_session(sess, max_turns=5),
            client=mock_client,
            memory=make_memory_runtime(store=ms, activity_log=al, keyword_index=ki),
            knowledge_registry=make_knowledge_registry(),
        )
    assert "wrapped_up" in out
    assert "未以无工具调用形式结束" not in out
    assert len(create_kwargs) == 2
    assert create_kwargs[0].get("tools") is not None
    assert create_kwargs[1].get("tools") is None


@pytest.mark.asyncio
async def test_execute_plan_phased_last_step_no_turns_left_still_warns() -> None:
    main, sess = make_ping_tool_registry()
    plan = StructuredPlan(
        summary="s",
        steps=[PlanStep(step_number=1, description="一步", expected_input="", expected_output="")],
        required_toolboxes=[],
    )

    async def only_tool_stream():
        class _Chunk:
            def __init__(self, delta):
                self.choices = [SimpleNamespace(delta=delta)]

        delta = SimpleNamespace(
            content=None,
            tool_calls=[
                SimpleNamespace(
                    index=0,
                    id="call_1",
                    function=SimpleNamespace(name="ping_tool", arguments="{}"),
                )
            ],
        )
        yield _Chunk(delta)

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(
        side_effect=lambda *_a, **_k: only_tool_stream()
    )
    ms, al, ki = mock_memory_bundle()
    with (
        patch("miniagent.core.executor._env_phased_execution_enabled", return_value=True),
        patch("miniagent.core.executor.EXECUTION_STEP_MAX_TURNS", 1),
    ):
        out = await execute_plan(
            plan,
            "hi",
            main,
            MagicMock(),
            agent_config_with_session(sess, max_turns=1),
            client=mock_client,
            memory=make_memory_runtime(store=ms, activity_log=al, keyword_index=ki),
            knowledge_registry=make_knowledge_registry(),
        )
    assert "「无工具调用」形式结束" in out


@pytest.mark.asyncio
async def test_execute_plan_phased_grace_still_tool_calls_warns() -> None:
    main, sess = make_ping_tool_registry()
    plan = StructuredPlan(
        summary="s",
        steps=[PlanStep(step_number=1, description="一步", expected_input="", expected_output="")],
        required_toolboxes=[],
    )

    async def tool_stream():
        class _Chunk:
            def __init__(self, delta):
                self.choices = [SimpleNamespace(delta=delta)]

        delta = SimpleNamespace(
            content=None,
            tool_calls=[
                SimpleNamespace(
                    index=0,
                    id="call_1",
                    function=SimpleNamespace(name="ping_tool", arguments="{}"),
                )
            ],
        )
        yield _Chunk(delta)

    mock_client = mock_streaming_client(extra_streams=[tool_stream, tool_stream])
    ms, al, ki = mock_memory_bundle()
    with (
        patch("miniagent.core.executor._env_phased_execution_enabled", return_value=True),
        patch("miniagent.core.executor.EXECUTION_STEP_MAX_TURNS", 1),
    ):
        out = await execute_plan(
            plan,
            "hi",
            main,
            MagicMock(),
            agent_config_with_session(sess, max_turns=2),
            client=mock_client,
            memory=make_memory_runtime(store=ms, activity_log=al, keyword_index=ki),
            knowledge_registry=make_knowledge_registry(),
        )
    assert "「无工具调用」形式结束" in out
    assert mock_client._call_count["n"] == 2


@pytest.mark.asyncio
async def test_execute_plan_ephemeral_session_skips_activity_log(state_dir) -> None:
    """后台子 session 不在 activity log 中落盘。"""
    from miniagent.types.config import AgentConfig, SessionBindingConfig

    main, sess = make_ping_tool_registry()
    mock_client = mock_streaming_client()
    ms, al, ki = mock_memory_bundle()
    session_key = "__bg__ephemeral"
    cfg = AgentConfig(
        max_turns=3,
        allow_parallel_tools=True,
        tool_selection_strategy="all",
        session_config=SessionBindingConfig(
            session_key=session_key,
            session_registry=sess,
        ),
        debug=False,
    )

    out = await execute_plan(
        empty_plan(),
        "hi",
        main,
        MagicMock(),
        cfg,
        client=mock_client,
        memory=make_memory_runtime(store=ms, activity_log=al, keyword_index=ki),
        knowledge_registry=make_knowledge_registry(),
    )
    assert "done" in out

    today = al._get_today_path()
    if os.path.isfile(today):
        assert session_key not in open(today, encoding="utf-8").read()


@pytest.mark.asyncio
async def test_execute_plan_respects_asyncio_cancel() -> None:
    """ReAct 循环在 asyncio 任务取消时抛出 CancelledError。"""
    import asyncio

    main, sess = make_ping_tool_registry()

    async def slow_stream(*args, **kwargs):
        await asyncio.sleep(30)
        return mock_streaming_client()  # unreachable

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(side_effect=slow_stream)
    ms, al, ki = mock_memory_bundle()

    task = asyncio.create_task(
        execute_plan(
            empty_plan(),
            "hi",
            main,
            MagicMock(),
            agent_config_with_session(sess),
            client=mock_client,
            memory=make_memory_runtime(store=ms, activity_log=al, keyword_index=ki),
            knowledge_registry=make_knowledge_registry(),
        )
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def _confirm_tool_schema(name: str = "danger_tool") -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": "danger",
            "parameters": {"type": "object", "properties": {}},
        },
    }


def _make_confirm_registry(*, handler=None) -> tuple[Any, list[int]]:
    from miniagent.infrastructure.registry import DefaultToolRegistry
    from miniagent.types.tool import ToolDefinition, ToolResult

    calls: list[int] = []

    async def _default_handler(args: dict, ctx) -> ToolResult:
        calls.append(1)
        return ToolResult(True, "executed")

    reg = DefaultToolRegistry()
    reg.register(
        "danger_tool",
        ToolDefinition(
            schema=_confirm_tool_schema(),
            handler=handler or _default_handler,
            permission="require-confirm",
            help_text="危险操作",
        ),
    )
    return reg, calls


@pytest.mark.asyncio
async def test_execute_plan_require_confirm_without_channel_denies() -> None:
    reg, calls = _make_confirm_registry()
    mock_client = mock_streaming_client(tool_name="danger_tool")
    ms, al, ki = mock_memory_bundle()
    finishes: list[tuple[str, str, bool]] = []

    async def on_finish(
        name: str,
        args_json: str,
        result: str,
        success: bool,
        *,
        thinking_header: str = "",
    ) -> None:
        finishes.append((name, result, success))

    await execute_plan(
        empty_plan(),
        "hi",
        reg,
        MagicMock(),
        agent_config_with_session(reg),
        client=mock_client,
        memory=make_memory_runtime(store=ms, activity_log=al, keyword_index=ki),
        knowledge_registry=make_knowledge_registry(),
        on_tool_finish=on_finish,
    )
    assert calls == []
    assert len(finishes) == 1
    assert finishes[0][0] == "danger_tool"
    assert finishes[0][2] is False
    assert "需要用户确认" in finishes[0][1]


@pytest.mark.asyncio
async def test_execute_plan_require_confirm_user_rejects() -> None:
    from miniagent.types.confirmation import ConfirmationResult

    reg, calls = _make_confirm_registry()
    channel = MagicMock()
    channel.request_confirmation = AsyncMock(return_value=ConfirmationResult.reject())
    mock_client = mock_streaming_client(tool_name="danger_tool")
    ms, al, ki = mock_memory_bundle()
    finishes: list[tuple[str, str, bool]] = []

    async def on_finish(
        name: str,
        args_json: str,
        result: str,
        success: bool,
        *,
        thinking_header: str = "",
    ) -> None:
        finishes.append((name, result, success))

    await execute_plan(
        empty_plan(),
        "hi",
        reg,
        MagicMock(),
        agent_config_with_session(reg),
        client=mock_client,
        memory=make_memory_runtime(store=ms, activity_log=al, keyword_index=ki),
        knowledge_registry=make_knowledge_registry(),
        confirmation_channel=channel,
        on_tool_finish=on_finish,
    )
    assert calls == []
    assert len(finishes) == 1
    assert "拒绝执行工具" in finishes[0][1]
    channel.request_confirmation.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_plan_require_confirm_auto_execute_skips() -> None:
    from miniagent.types.config import AgentConfig, SessionBindingConfig

    reg, calls = _make_confirm_registry()
    mock_client = mock_streaming_client(tool_name="danger_tool")
    ms, al, ki = mock_memory_bundle()
    cfg = AgentConfig(
        max_turns=3,
        allow_parallel_tools=True,
        tool_selection_strategy="all",
        session_config=SessionBindingConfig(session_registry=reg),
        auto_execute_confirmed=True,
    )
    out = await execute_plan(
        empty_plan(),
        "hi",
        reg,
        MagicMock(),
        cfg,
        client=mock_client,
        memory=make_memory_runtime(store=ms, activity_log=al, keyword_index=ki),
        knowledge_registry=make_knowledge_registry(),
    )
    assert "done" in out
    assert calls == [1]


@pytest.mark.asyncio
async def test_execute_plan_require_confirm_user_approves() -> None:
    from miniagent.types.confirmation import ConfirmationResult

    reg, calls = _make_confirm_registry()
    channel = MagicMock()
    channel.request_confirmation = AsyncMock(return_value=ConfirmationResult.confirm())
    mock_client = mock_streaming_client(tool_name="danger_tool")
    ms, al, ki = mock_memory_bundle()
    out = await execute_plan(
        empty_plan(),
        "hi",
        reg,
        MagicMock(),
        agent_config_with_session(reg),
        client=mock_client,
        memory=make_memory_runtime(store=ms, activity_log=al, keyword_index=ki),
        knowledge_registry=make_knowledge_registry(),
        confirmation_channel=channel,
    )
    assert "done" in out
    assert calls == [1]
    channel.request_confirmation.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_plan_phased_last_step_persists_session_memory() -> None:
    """分步模式最后一步完成时应落盘会话记忆（回归：曾调用未定义的 _save_session_memory）。"""
    from miniagent.types.config import AgentConfig, SessionBindingConfig

    main, sess = make_ping_tool_registry()
    plan = StructuredPlan(
        summary="s",
        steps=[PlanStep(step_number=1, description="一步", expected_input="", expected_output="")],
        required_toolboxes=[],
    )
    mock_client = mock_streaming_client(final_text="wrapped_up")
    ms, al, ki = mock_memory_bundle()
    mc = MagicMock()
    mc.inject_memory_to_messages = AsyncMock(return_value=([], {}))
    mc.save_memory_after_turn = AsyncMock()
    cfg = AgentConfig(
        max_turns=5,
        allow_parallel_tools=True,
        tool_selection_strategy="all",
        session_config=SessionBindingConfig(
            session_key="test-session",
            session_registry=sess,
        ),
        debug=False,
    )
    with patch("miniagent.core.executor._env_phased_execution_enabled", return_value=True):
        out = await execute_plan(
            plan,
            "hi",
            main,
            MagicMock(),
            cfg,
            client=mock_client,
            memory=make_memory_runtime(
                store=ms,
                activity_log=al,
                keyword_index=ki,
                context=mc,
            ),
            knowledge_registry=make_knowledge_registry(),
        )
    assert "wrapped_up" in out
    mc.save_memory_after_turn.assert_awaited_once()
    al.log_final_reply.assert_called_once_with("test-session", "wrapped_up")

