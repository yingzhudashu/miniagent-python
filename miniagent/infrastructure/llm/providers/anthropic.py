"""Optional Anthropic Messages provider."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

from miniagent.contracts.llm import (
    LLMCompletion,
    LLMStreamEvent,
    LLMToolCallDelta,
    ModelDescriptor,
)
from miniagent.infrastructure.llm.providers.common import (
    field,
    normalize_provider_error,
    tool_call,
    usage_from_fields,
)


def _anthropic_messages(
    messages: list[dict[str, Any]],
) -> tuple[str | None, list[dict[str, Any]]]:
    system_parts: list[str] = []
    result: list[dict[str, Any]] = []
    pending_tool_results: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role") or "")
        content = message.get("content")
        if role in ("system", "developer"):
            if isinstance(content, str):
                system_parts.append(content)
            continue
        if role == "tool":
            pending_tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": str(message.get("tool_call_id") or ""),
                    "content": str(content or ""),
                    "is_error": bool(message.get("is_error", False)),
                }
            )
            continue
        if pending_tool_results:
            result.append({"role": "user", "content": pending_tool_results})
            pending_tool_results = []
        if role not in ("user", "assistant"):
            continue
        blocks: Any = content if isinstance(content, list) else str(content or "")
        if role == "assistant" and message.get("tool_calls"):
            block_list = [] if not blocks else ([{"type": "text", "text": blocks}] if isinstance(blocks, str) else list(blocks))
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                arguments = function.get("arguments") or "{}"
                import json

                try:
                    parsed = json.loads(arguments) if isinstance(arguments, str) else arguments
                except (TypeError, json.JSONDecodeError):
                    parsed = {}
                block_list.append(
                    {
                        "type": "tool_use",
                        "id": str(call.get("id") or ""),
                        "name": str(function.get("name") or ""),
                        "input": parsed,
                    }
                )
            blocks = block_list
        result.append({"role": role, "content": blocks})
    if pending_tool_results:
        result.append({"role": "user", "content": pending_tool_results})
    return ("\n\n".join(system_parts) or None), result


def _anthropic_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    result = []
    for tool in tools or []:
        function = tool.get("function") if tool.get("type") == "function" else tool
        if not isinstance(function, dict):
            continue
        result.append(
            {
                "name": function.get("name"),
                "description": function.get("description", ""),
                "input_schema": function.get("parameters") or {"type": "object", "properties": {}},
            }
        )
    return result


class AnthropicProvider:
    """Anthropic adapter loaded only when the optional SDK is installed."""

    def __init__(
        self,
        provider_id: str,
        *,
        api_key: str,
        base_url: str | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float = 120.0,
        max_retries: int = 2,
    ) -> None:
        try:
            from anthropic import AsyncAnthropic
        except ImportError as error:
            raise RuntimeError(
                "Anthropic provider requires: pip install 'miniagent-python[providers]'"
            ) from error
        kwargs: dict[str, Any] = {
            "api_key": api_key,
            "default_headers": dict(headers or {}),
            "timeout": timeout,
            "max_retries": max_retries,
        }
        if base_url:
            kwargs["base_url"] = base_url
        self._provider_id = provider_id
        self._client = AsyncAnthropic(**kwargs)

    @property
    def provider_id(self) -> str:
        return self._provider_id

    async def list_models(self) -> Sequence[ModelDescriptor]:
        models_api = getattr(self._client, "models", None)
        if models_api is None or not callable(getattr(models_api, "list", None)):
            return ()
        response = await models_api.list()
        return tuple(
            ModelDescriptor(
                profile=f"{self.provider_id}:{field(item, 'id')}",
                provider=self.provider_id,
                model=str(field(item, "id")),
                api="anthropic_messages",
            )
            for item in (field(response, "data", ()) or ())
            if field(item, "id")
        )

    def _kwargs(
        self,
        model: ModelDescriptor,
        messages: list[dict[str, Any]],
        params: dict[str, Any],
        tools: list[dict[str, Any]] | None,
        *,
        stream: bool,
    ) -> dict[str, Any]:
        system, converted = _anthropic_messages(messages)
        kwargs: dict[str, Any] = {
            "model": model.model,
            "messages": converted,
            "max_tokens": int(params.get("max_tokens", model.max_output_tokens)),
            "stream": stream,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = _anthropic_tools(tools)
        if params.get("temperature") is not None:
            kwargs["temperature"] = params["temperature"]
        if params.get("top_p") is not None:
            kwargs["top_p"] = params["top_p"]
        return kwargs

    async def create_completion(
        self,
        model: ModelDescriptor,
        *,
        messages: list[dict[str, Any]],
        params: dict[str, Any],
        tools: list[dict[str, Any]] | None = None,
        json_mode: bool = False,
    ) -> LLMCompletion:
        try:
            response = await self._client.messages.create(
                **self._kwargs(model, messages, params, tools, stream=False)
            )
        except Exception as error:
            raise normalize_provider_error(error, self.provider_id) from None
        text_parts: list[str] = []
        calls = []
        for block in field(response, "content", ()) or ():
            kind = field(block, "type")
            if kind == "text":
                text_parts.append(str(field(block, "text", "") or ""))
            elif kind == "tool_use":
                calls.append(
                    tool_call(field(block, "id"), field(block, "name"), field(block, "input"))
                )
        raw_usage = field(response, "usage")
        usage = usage_from_fields(
            input_tokens=field(raw_usage, "input_tokens", 0),
            output_tokens=field(raw_usage, "output_tokens", 0),
            cache_read_tokens=field(raw_usage, "cache_read_input_tokens", 0),
            cache_write_tokens=field(raw_usage, "cache_creation_input_tokens", 0),
        )
        return LLMCompletion(
            content="".join(text_parts) or None,
            tool_calls=calls,
            usage=usage,
            model=str(field(response, "model", model.model)),
            finish_reason=str(field(response, "stop_reason", "") or "") or None,
        )

    async def stream_completion(
        self,
        model: ModelDescriptor,
        *,
        messages: list[dict[str, Any]],
        params: dict[str, Any],
        tools: list[dict[str, Any]] | None = None,
        json_mode: bool = False,
    ) -> AsyncIterator[LLMStreamEvent]:
        try:
            stream = await self._client.messages.create(
                **self._kwargs(model, messages, params, tools, stream=True)
            )
            tool_indexes: dict[int, dict[str, str]] = {}
            async for event in stream:
                kind = str(field(event, "type", ""))
                index = int(field(event, "index", 0) or 0)
                block = field(event, "content_block")
                delta = field(event, "delta")
                if kind == "content_block_start" and field(block, "type") == "tool_use":
                    state = tool_indexes.setdefault(index, {"id": "", "name": ""})
                    state["id"] = str(field(block, "id", "") or "")
                    state["name"] = str(field(block, "name", "") or "")
                    yield LLMStreamEvent(
                        event_type="tool_call_delta",
                        tool_call_delta=LLMToolCallDelta(
                            index=index, id=state["id"], name=state["name"]
                        ),
                    )
                elif kind == "content_block_delta" and field(delta, "type") == "text_delta":
                    yield LLMStreamEvent(
                        event_type="text_delta",
                        content_delta=str(field(delta, "text", "") or ""),
                    )
                elif kind == "content_block_delta" and field(delta, "type") in (
                    "thinking_delta",
                    "signature_delta",
                ):
                    thinking = field(delta, "thinking", "")
                    if thinking:
                        yield LLMStreamEvent(
                            event_type="thinking_delta", thinking_delta=str(thinking)
                        )
                elif kind == "content_block_delta" and field(delta, "type") == "input_json_delta":
                    state = tool_indexes.get(index, {"id": "", "name": ""})
                    yield LLMStreamEvent(
                        event_type="tool_call_delta",
                        tool_call_delta=LLMToolCallDelta(
                            index=index,
                            id=state["id"],
                            name=state["name"],
                            arguments=str(field(delta, "partial_json", "") or ""),
                        ),
                    )
                elif kind == "message_delta":
                    usage = field(event, "usage")
                    yield LLMStreamEvent(
                        usage=usage_from_fields(
                            output_tokens=field(usage, "output_tokens", 0)
                        )
                    )
            yield LLMStreamEvent(event_type="completed", completed=True, status="completed")
        except Exception as error:
            raise normalize_provider_error(error, self.provider_id) from None

    async def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            result = close()
            if hasattr(result, "__await__"):
                await result


__all__ = ["AnthropicProvider"]
