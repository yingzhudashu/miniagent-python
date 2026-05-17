"""Shared mocks for execute_plan integration tests."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from miniagent.infrastructure.registry import DefaultToolRegistry
from miniagent.types.config import AgentConfig
from miniagent.types.planning import StructuredPlan
from miniagent.types.tool import ToolContext, ToolDefinition, ToolResult


def make_ping_tool_registry() -> tuple[DefaultToolRegistry, DefaultToolRegistry]:
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
    return main, sess


def mock_memory_bundle() -> tuple[MagicMock, MagicMock, MagicMock]:
    ms = MagicMock()
    al = MagicMock()
    ki = MagicMock()
    ki.get_stats.return_value = {"total_keywords": 0}
    return ms, al, ki


def agent_config_with_session(
    sess: DefaultToolRegistry,
    *,
    max_turns: int = 3,
) -> AgentConfig:
    return AgentConfig(
        max_turns=max_turns,
        session_key=None,
        allow_parallel_tools=True,
        tool_selection_strategy="all",
        session_registry=sess,
    )


def mock_streaming_client(
    *,
    tool_name: str = "ping_tool",
    tool_args: str = "{}",
    final_text: str = "done",
    extra_streams: list[Any] | None = None,
) -> MagicMock:
    """First stream: tool call; following streams: text (or use extra_streams)."""
    mock_client = MagicMock()

    class _Chunk:
        def __init__(self, delta: Any, usage: Any = None) -> None:
            self.choices = [SimpleNamespace(delta=delta)]
            self.usage = usage

    streams = list(extra_streams or [])

    async def default_tool_stream():
        delta = SimpleNamespace(
            content=None,
            tool_calls=[
                SimpleNamespace(
                    index=0,
                    id="call_1",
                    function=SimpleNamespace(name=tool_name, arguments=tool_args),
                )
            ],
        )
        yield _Chunk(delta)

    async def default_text_stream():
        yield _Chunk(SimpleNamespace(content=final_text, tool_calls=None))

    if not streams:
        streams = [default_tool_stream, default_text_stream]

    call_count = {"n": 0}

    async def create_side_effect(*_a: object, **_k: object) -> Any:
        idx = call_count["n"]
        call_count["n"] += 1
        if idx < len(streams):
            return streams[idx]()
        return default_text_stream()

    mock_client.chat.completions.create = AsyncMock(side_effect=create_side_effect)
    mock_client._call_count = call_count  # type: ignore[attr-defined]
    return mock_client


def empty_plan() -> StructuredPlan:
    return StructuredPlan(summary="s", steps=[], required_toolboxes=[])
