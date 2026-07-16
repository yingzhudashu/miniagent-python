"""Focused test doubles shared by executor integration tests."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from miniagent.llm.gateway import LLMGateway
from miniagent.llm.providers.openai_transport import (
    create_completion as create_openai_completion,
)
from miniagent.llm.providers.openai_transport import (
    stream_completion as stream_openai_completion,
)
from miniagent.llm.types import ModelCapabilities, ModelDescriptor


class MockGateway:
    """Test-only Gateway double around a raw OpenAI-compatible SDK mock."""

    def __init__(
        self, raw: MagicMock, *, responses: bool = False, vision: bool = False
    ) -> None:
        self.raw = raw
        self.chat = raw.chat
        self.responses = raw.responses
        self._call_count = getattr(raw, "_call_count", {"n": 0})
        self.descriptor = ModelDescriptor(
            profile="test",
            provider="test",
            model="test-model",
            api="openai_responses" if responses else "openai_chat_completions",
            capabilities=ModelCapabilities(vision=vision),
            defaults={"temperature": 0.7, "top_p": 1.0, "max_tokens": 4096},
        )
        self.catalog = self

    def get(self, _profile: str):
        return self.descriptor

    def model_for_role(self, _role: str = "default") -> ModelDescriptor:
        return self.descriptor

    async def create_completion(self, **kwargs: Any):
        return await create_openai_completion(
            self.raw,
            messages=kwargs["messages"],
            params=LLMGateway._provider_params(kwargs["params"], self.descriptor),
            tools=kwargs.get("tools"),
            json_mode=kwargs.get("json_mode", False),
            wire_api="responses" if self.descriptor.api == "openai_responses" else "chat_completions",
        )

    def stream_completion(self, **kwargs: Any):
        return stream_openai_completion(
            self.raw,
            messages=kwargs["messages"],
            params=LLMGateway._provider_params(kwargs["params"], self.descriptor),
            tools=kwargs.get("tools"),
            json_mode=kwargs.get("json_mode", False),
            wire_api="responses" if self.descriptor.api == "openai_responses" else "chat_completions",
        )


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


def mock_streaming_client(
    *,
    tool_name: str = "ping_tool",
    tool_args: str = "{}",
    final_text: str = "done",
    extra_streams: list[Any] | None = None,
) -> MockGateway:
    """Create a client that emits a tool call followed by final text."""
    client = MagicMock()

    class _Chunk:
        def __init__(self, delta: Any, usage: Any = None) -> None:
            self.choices = [SimpleNamespace(delta=delta)]
            self.usage = usage

    streams = list(extra_streams or [])

    async def default_tool_stream():
        yield _Chunk(
            SimpleNamespace(
                content=None,
                tool_calls=[
                    SimpleNamespace(
                        index=0,
                        id="call_1",
                        function=SimpleNamespace(name=tool_name, arguments=tool_args),
                    )
                ],
            )
        )

    async def default_text_stream():
        yield _Chunk(SimpleNamespace(content=final_text, tool_calls=None))

    if not streams:
        streams = [default_tool_stream, default_text_stream]

    call_count = {"n": 0}

    async def create_side_effect(*_args: object, **_kwargs: object) -> Any:
        index = call_count["n"]
        call_count["n"] += 1
        if index < len(streams):
            return streams[index]()
        return default_text_stream()

    client.chat.completions.create = AsyncMock(side_effect=create_side_effect)
    client._call_count = call_count
    return MockGateway(client)


__all__ = [
    "agent_config_with_session",
    "empty_plan",
    "make_ping_tool_registry",
    "mock_memory_bundle",
    "mock_streaming_client",
]
