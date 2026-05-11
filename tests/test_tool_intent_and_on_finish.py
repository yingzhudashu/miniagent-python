"""工具意图截断与 on_tool_finish 四参向后兼容。"""

from __future__ import annotations

import pytest


def test_extract_tool_intent_truncation_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    from miniagent.core.executor import _extract_tool_intent

    monkeypatch.setenv("MINIAGENT_TOOL_INTENT_MAX_CHARS", "8")
    long_cmd = "x" * 40
    s = _extract_tool_intent("exec_command", {"command": long_cmd})
    assert s.startswith("执行命令: xxxxxxxx")
    assert "…（共 40 字）" in s


def test_extract_tool_intent_zero_means_no_clip(monkeypatch: pytest.MonkeyPatch) -> None:
    from miniagent.core.executor import _extract_tool_intent

    monkeypatch.setenv("MINIAGENT_TOOL_INTENT_MAX_CHARS", "0")
    long_cmd = "y" * 500
    s = _extract_tool_intent("exec_command", {"command": long_cmd})
    assert s == f"执行命令: {long_cmd}"


@pytest.mark.asyncio
async def test_execute_plan_on_tool_finish_four_positional_only() -> None:
    """仅接受四位置参数的回调不因 thinking_header 崩掉。"""
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
        return ToolResult(True, "out")

    sess.register(
        "ping2",
        ToolDefinition(
            schema={
                "type": "function",
                "function": {
                    "name": "ping2",
                    "description": "t",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            handler=fake_handler,
            permission="allowlist",
            help_text="",
            toolbox=None,
        ),
    )

    class _Chunk:
        def __init__(self, delta, usage=None):
            self.choices = [SimpleNamespace(delta=delta)]
            self.usage = usage

    n = {"c": 0}

    async def create_side_effect(*args, **kwargs):
        n["c"] += 1
        if n["c"] == 1:

            async def stream1():
                yield _Chunk(
                    SimpleNamespace(
                        content=None,
                        tool_calls=[
                            SimpleNamespace(
                                index=0,
                                id="c1",
                                function=SimpleNamespace(name="ping2", arguments="{}"),
                            )
                        ],
                    )
                )

            return stream1()

        async def stream2():
            yield _Chunk(SimpleNamespace(content="ok", tool_calls=None))

        return stream2()

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(side_effect=create_side_effect)

    hits: list[tuple[str, str, str, bool]] = []

    async def on_finish_four(name: str, args_json: str, result: str, success: bool) -> None:
        hits.append((name, args_json, result, success))

    ac = AgentConfig(
        max_turns=3,
        session_key=None,
        allow_parallel_tools=True,
        tool_selection_strategy="all",
        session_registry=sess,
    )
    ki = MagicMock()
    ki.get_stats.return_value = {"total_keywords": 0}
    out = await execute_plan(
        StructuredPlan(summary="s", steps=[], required_toolboxes=[]),
        "hi",
        main,
        MagicMock(),
        ac,
        client=mock_client,
        memory_store=MagicMock(),
        activity_log=MagicMock(),
        keyword_index=ki,
        on_tool_finish=on_finish_four,
    )
    assert "ok" in out
    assert len(hits) == 1
    assert hits[0][0] == "ping2"
    assert hits[0][2] == "out"
    assert hits[0][3] is True
