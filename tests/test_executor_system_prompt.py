"""执行器 system prompt 合并与规划 JSON 解析。"""

from __future__ import annotations

import pytest

from miniagent.core.executor import build_execution_system_prompt
from miniagent.core.planner import _format_toolbox_tool_names, _parse_plan_json
from miniagent.infrastructure.registry import DefaultToolRegistry
from miniagent.types.tool import ToolDefinition


def test_build_execution_system_prompt_order() -> None:
    s = build_execution_system_prompt(
        agent_identity="ID",
        caller_system_prompt="SKILL",
        plan_summary="TASK",
        keyword_context="KW",
    )
    assert s.index("ID") < s.index("SKILL") < s.index("当前任务：TASK") < s.index("KW")


def test_build_execution_system_prompt_skips_empty_caller() -> None:
    s = build_execution_system_prompt(
        agent_identity="ID",
        caller_system_prompt=None,
        plan_summary="T",
        keyword_context=None,
    )
    assert "ID" in s
    assert "当前任务：T" in s
    assert s.count("\n\n") >= 1


def test_parse_plan_json_brace_slice() -> None:
    raw = '说明文字\n{"summary":"x","steps":[],"requiredToolboxes":[]}\n尾部'
    data = _parse_plan_json(raw)
    assert data["summary"] == "x"
    assert data["requiredToolboxes"] == []


def test_format_toolbox_tool_names() -> None:
    reg = DefaultToolRegistry()

    async def _h(args, ctx):
        from miniagent.types.tool import ToolResult

        return ToolResult(True, "")

    reg.register(
        "read_file",
        ToolDefinition(
            schema={"type": "function", "function": {"name": "read_file", "parameters": {}}},
            handler=_h,
            permission="sandbox",
            help_text="",
            toolbox="file_read",
        ),
    )
    reg.register(
        "noop",
        ToolDefinition(
            schema={"type": "function", "function": {"name": "noop", "parameters": {}}},
            handler=_h,
            permission="sandbox",
            help_text="",
            toolbox=None,
        ),
    )
    hint = _format_toolbox_tool_names(reg, ["file_read"])
    assert "read_file" in hint
    assert "__core__" in hint
    assert "noop" in hint


@pytest.mark.asyncio
async def test_execute_plan_uses_session_registry_for_tools() -> None:
    """session_registry 中的工具应能被执行（与 effective_registry 一致）。"""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from miniagent.core.executor import execute_plan
    from miniagent.infrastructure.registry import DefaultToolRegistry
    from miniagent.types.config import AgentConfig
    from miniagent.types.planning import StructuredPlan
    from miniagent.types.tool import ToolContext, ToolDefinition, ToolResult

    main = DefaultToolRegistry()
    sess = DefaultToolRegistry()

    async def fake_handler(args: dict, ctx: ToolContext) -> ToolResult:
        return ToolResult(True, "ok")

    ping_schema = {
        "type": "function",
        "function": {
            "name": "ping_tool",
            "description": "test",
            "parameters": {"type": "object", "properties": {}},
        },
    }
    sess.register(
        "ping_tool",
        ToolDefinition(
            schema=ping_schema,
            handler=fake_handler,
            permission="allowlist",
            help_text="",
            toolbox=None,
        ),
    )

    plan = StructuredPlan(summary="s", steps=[], required_toolboxes=[])

    mock_client = MagicMock()

    class _Chunk:
        def __init__(self, delta, usage=None):
            self.choices = [SimpleNamespace(delta=delta)]
            self.usage = usage

    call_count = {"n": 0}

    async def create_side_effect(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:

            async def stream1():
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

            return stream1()

        async def stream2():
            yield _Chunk(SimpleNamespace(content="done", tool_calls=None))

        return stream2()

    mock_client.chat.completions.create = AsyncMock(side_effect=create_side_effect)

    ac = AgentConfig(
        max_turns=3,
        session_key=None,
        allow_parallel_tools=True,
        tool_selection_strategy="all",
        session_registry=sess,
    )

    ms = MagicMock()
    al = MagicMock()
    ki = MagicMock()
    ki.get_stats.return_value = {"total_keywords": 0}

    out = await execute_plan(
        plan,
        "hi",
        main,
        MagicMock(),
        ac,
        client=mock_client,
        memory_store=ms,
        activity_log=al,
        keyword_index=ki,
    )
    assert "done" in out
