"""LLM test doubles shared by focused agent tests."""

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
    """Gateway double around a raw OpenAI-compatible SDK mock."""

    def __init__(
        self,
        raw: MagicMock,
        *,
        responses: bool = False,
        vision: bool = False,
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
            wire_api=(
                "responses"
                if self.descriptor.api == "openai_responses"
                else "chat_completions"
            ),
        )

    def stream_completion(self, **kwargs: Any):
        return stream_openai_completion(
            self.raw,
            messages=kwargs["messages"],
            params=LLMGateway._provider_params(kwargs["params"], self.descriptor),
            tools=kwargs.get("tools"),
            json_mode=kwargs.get("json_mode", False),
            wire_api=(
                "responses"
                if self.descriptor.api == "openai_responses"
                else "chat_completions"
            ),
        )


def mock_streaming_client(
    *,
    tool_name: str = "ping_tool",
    tool_args: str = "{}",
    final_text: str = "done",
    extra_streams: list[Any] | None = None,
) -> MockGateway:
    """Create a gateway that emits a tool call followed by final text."""
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


__all__ = ["MockGateway", "mock_streaming_client"]
