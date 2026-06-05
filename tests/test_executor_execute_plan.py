"""execute_plan 集成测试（mock OpenAI 流式客户端）。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from miniagent.core.executor import execute_plan
from miniagent.types.planning import PlanStep, StructuredPlan
from tests.executor_helpers import (
    agent_config_with_session,
    empty_plan,
    make_ping_tool_registry,
    mock_memory_bundle,
    mock_streaming_client,
)


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
        memory_store=ms,
        activity_log=al,
        keyword_index=ki,
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
        memory_store=ms,
        activity_log=al,
        keyword_index=ki,
        on_tool_finish=on_finish,
    )
    assert "done" in out
    assert len(finishes) == 1
    assert finishes[0][0] == "ping_tool"
    assert finishes[0][3] is True
    assert finishes[0][4] == "[执行]"


@pytest.mark.asyncio
async def test_execute_plan_phased_last_step_grace_synthesis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MINIAGENT_PHASED_EXECUTION", "1")
    monkeypatch.setenv("MINIAGENT_EXECUTION_STEP_MAX_TURNS", "1")
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
    out = await execute_plan(
        plan,
        "hi",
        main,
        MagicMock(),
        agent_config_with_session(sess, max_turns=5),
        client=mock_client,
        memory_store=ms,
        activity_log=al,
        keyword_index=ki,
    )
    assert "wrapped_up" in out
    assert "未以无工具调用形式结束" not in out
    assert len(create_kwargs) == 2
    assert create_kwargs[0].get("tools") is not None
    assert create_kwargs[1].get("tools") is None


@pytest.mark.asyncio
async def test_execute_plan_phased_last_step_no_turns_left_still_warns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MINIAGENT_PHASED_EXECUTION", "1")
    monkeypatch.setenv("MINIAGENT_EXECUTION_STEP_MAX_TURNS", "1")
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
    out = await execute_plan(
        plan,
        "hi",
        main,
        MagicMock(),
        agent_config_with_session(sess, max_turns=1),
        client=mock_client,
        memory_store=ms,
        activity_log=al,
        keyword_index=ki,
    )
    assert "「无工具调用」形式结束" in out


@pytest.mark.asyncio
async def test_execute_plan_phased_grace_still_tool_calls_warns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MINIAGENT_PHASED_EXECUTION", "1")
    monkeypatch.setenv("MINIAGENT_EXECUTION_STEP_MAX_TURNS", "1")
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
    out = await execute_plan(
        plan,
        "hi",
        main,
        MagicMock(),
        agent_config_with_session(sess, max_turns=2),
        client=mock_client,
        memory_store=ms,
        activity_log=al,
        keyword_index=ki,
    )
    assert "「无工具调用」形式结束" in out
    assert mock_client._call_count["n"] == 2
