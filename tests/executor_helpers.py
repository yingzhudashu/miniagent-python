"""Explicit collaborator factories for executor integration tests."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock


def make_ping_tool_registry() -> tuple[Any, Any]:
    """Create main/session registries with one successful session tool."""
    from miniagent.agent.types.tool import ToolDefinition, ToolResult
    from miniagent.assistant.infrastructure.registry import DefaultToolRegistry

    async def handler(_args: dict[str, Any], _ctx: Any) -> ToolResult:
        return ToolResult(success=True, content="pong")

    ping_tool = ToolDefinition(
        schema={
            "type": "function",
            "function": {
                "name": "ping_tool",
                "description": "Return pong",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Optional path"},
                    },
                    "required": [],
                },
            },
        },
        handler=handler,
        permission="allowlist",
        help_text="Return pong",
        toolbox="filesystem",
    )
    main = DefaultToolRegistry()
    session = DefaultToolRegistry()
    session.register("ping_tool", ping_tool)
    return main, session


def mock_memory_bundle() -> tuple[MagicMock, MagicMock, MagicMock]:
    """Create the three collaborators overridden by executor tests."""
    store = MagicMock()
    activity_log = MagicMock()
    activity_log.log_session_start = AsyncMock()
    activity_log.log_llm_call = AsyncMock()
    activity_log.log_tool_call = AsyncMock()
    activity_log.log_final_reply = AsyncMock()
    activity_log.log_incomplete = AsyncMock()
    keyword_index = MagicMock()
    keyword_index.get_stats.return_value = {"total_keywords": 0}
    return store, activity_log, keyword_index


def agent_config_with_session(
    session_registry: Any,
    *,
    max_turns: int = 3,
    debug: bool = False,
) -> Any:
    """Create an AgentConfig bound to a session tool registry."""
    from miniagent.agent.types.config import AgentConfig, SessionBindingConfig

    return AgentConfig(
        max_turns=max_turns,
        allow_parallel_tools=True,
        tool_selection_strategy="all",
        session_config=SessionBindingConfig(session_registry=session_registry),
        debug=debug,
    )


def empty_plan() -> Any:
    """Create the minimal direct-execution plan."""
    from miniagent.agent.types.planning import StructuredPlan

    return StructuredPlan(summary="s", steps=[], required_toolboxes=[])


__all__ = [
    "agent_config_with_session",
    "empty_plan",
    "make_ping_tool_registry",
    "mock_memory_bundle",
]
