"""Protocol-neutral OpenAI transport for Chat Completions and Responses."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

from miniagent.llm.capabilities import (
    apply_learned_capabilities as _apply_learned_capabilities,
)
from miniagent.llm.capabilities import (
    learn_unsupported_params as _learn_unsupported_params,
)
from miniagent.llm.types import (
    LLMCompletion,
    LLMFailureInfo,
    LLMFunctionCall,
    LLMStreamEvent,
    LLMToolCall,
    LLMToolCallDelta,
    LLMTransportError,
    OpenAIWireAPI,
)


def _wire_api(override: OpenAIWireAPI | None) -> OpenAIWireAPI:
    return override or "chat_completions"


def _normalize_gateway_error(exc: Exception) -> LLMTransportError | None:
    message = str(exc)
    status = getattr(exc, "status_code", None)
    lowered = message.lower()
    if status == 403 and ("cloudflare" in lowered or "attention required" in lowered):
        return LLMTransportError(
            "LLM endpoint rejected the SDK client at its Cloudflare/WAF layer (HTTP 403). "
            "Configure the provider User-Agent header with a value accepted by the endpoint."
        )
    if "no_available_providers" in lowered:
        return LLMTransportError(
            "LLM endpoint has no provider available for this model/client "
            "(no_available_providers). Check the selected provider/profile and gateway access."
        )
    return None


async def _await_with_gateway_errors(
    call: Any,
    *,
    client: Any | None = None,
    params: dict[str, Any] | None = None,
    wire_api: OpenAIWireAPI | None = None,
) -> Any:
    try:
        return await call
    except Exception as exc:
        if client is not None and params is not None and wire_api is not None:
            _learn_unsupported_params(client, params, wire_api, exc)
        normalized = _normalize_gateway_error(exc)
        if normalized is not None:
            raise normalized from None
        raise


def _chat_params(params: dict[str, Any], *, stream: bool) -> dict[str, Any]:
    result = dict(params)
    result["stream"] = stream
    result.pop("_thinking_level", None)
    result.pop("_thinking_budget", None)
    result.pop("thinking_level", None)
    result.pop("thinking_budget", None)
    return result


def _reasoning_effort(level: Any, *, json_mode: bool = False) -> str | None:
    normalized = str(level or "").strip().lower()
    # Some Responses gateways interpret an omitted/disabled effort as their costly
    # default reasoning mode and may return reasoning-only output for JSON controls.
    if not normalized or normalized in ("none", "disabled", "off"):
        return "low" if json_mode else None
    if normalized in ("light", "low"):
        return "low"
    if normalized == "medium":
        return "medium"
    if normalized in ("heavy", "high"):
        return "high"
    return None


def _responses_params(
    params: dict[str, Any], *, stream: bool, json_mode: bool = False
) -> dict[str, Any]:
    source = dict(params)
    source.pop("stream", None)
    source.pop("response_format", None)
    thinking_level = source.pop("_thinking_level", None)
    raw_thinking_level = source.pop("thinking_level", None)
    if thinking_level is None:
        thinking_level = raw_thinking_level
    source.pop("_thinking_budget", None)
    source.pop("thinking_budget", None)
    if "max_tokens" in source:
        source["max_output_tokens"] = source.pop("max_tokens")
    effort = _reasoning_effort(thinking_level, json_mode=json_mode)
    if effort:
        source["reasoning"] = {"effort": effort}
    if json_mode:
        text_config = source.get("text")
        normalized_text = dict(text_config) if isinstance(text_config, dict) else {}
        normalized_text.setdefault("format", {"type": "json_object"})
        source["text"] = normalized_text
    source["stream"] = stream
    return source


def _content_for_responses(content: Any) -> Any:
    if not isinstance(content, list):
        return content if content is not None else ""
    converted: list[dict[str, Any]] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        part_type = part.get("type")
        if part_type in ("text", "input_text", "output_text"):
            converted.append({"type": "input_text", "text": str(part.get("text", ""))})
        elif part_type in ("image_url", "input_image"):
            image_value = part.get("image_url")
            if isinstance(image_value, dict):
                image_value = image_value.get("url")
            if image_value:
                converted.append({"type": "input_image", "image_url": str(image_value)})
    return converted


def messages_to_responses_input(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert Chat-style history into stateless Responses input items."""
    result: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role", "user"))
        if role == "tool":
            result.append(
                {
                    "type": "function_call_output",
                    "call_id": str(message.get("tool_call_id", "")),
                    "output": str(message.get("content", "")),
                }
            )
            continue

        content = message.get("content")
        if content not in (None, "", []):
            result.append({"role": role, "content": _content_for_responses(content)})

        if role == "assistant":
            for tool_call in message.get("tool_calls") or []:
                if not isinstance(tool_call, dict):
                    continue
                function = tool_call.get("function") or {}
                if not isinstance(function, dict):
                    continue
                result.append(
                    {
                        "type": "function_call",
                        "call_id": str(tool_call.get("id", "")),
                        "name": str(function.get("name", "")),
                        "arguments": str(function.get("arguments", "{}")),
                    }
                )
    return result


def tools_to_responses(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Flatten Chat function tools into the Responses tool schema."""
    converted: list[dict[str, Any]] = []
    for tool in tools or []:
        if tool.get("type") != "function" or not isinstance(tool.get("function"), dict):
            continue
        function = tool["function"]
        item: dict[str, Any] = {
            "type": "function",
            "name": str(function.get("name", "")),
            "parameters": function.get("parameters") or {"type": "object", "properties": {}},
        }
        if function.get("description") is not None:
            item["description"] = str(function["description"])
        if function.get("strict") is not None:
            item["strict"] = bool(function["strict"])
        converted.append(item)
    return converted


def _tool_call(call_id: Any, name: Any, arguments: Any) -> LLMToolCall:
    arguments_text = str(arguments or "{}")
    try:
        parsed = json.loads(arguments_text)
    except (TypeError, json.JSONDecodeError):
        parsed = {}
    return LLMToolCall(
        id=str(call_id or ""),
        function=LLMFunctionCall(name=str(name or ""), arguments=arguments_text),
        _args_dict=parsed if isinstance(parsed, dict) else {},
    )


def _field(value: Any, name: str, default: Any = None) -> Any:
    """Read one field from either an SDK model or a dictionary test double."""
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _response_output_text(response: Any) -> str | None:
    """Extract Responses text, including gateways that omit ``output_text``."""
    direct = _field(response, "output_text")
    if isinstance(direct, str) and direct:
        return direct

    fragments: list[str] = []
    for item in _field(response, "output", []) or []:
        if _field(item, "type") != "message":
            continue
        for part in _field(item, "content", []) or []:
            if _field(part, "type") not in ("output_text", "text"):
                continue
            text = _field(part, "text")
            if isinstance(text, str) and text:
                fragments.append(text)
    return "".join(fragments) or (direct if isinstance(direct, str) else None)


async def create_completion(
    client: Any,
    *,
    messages: list[dict[str, Any]],
    params: dict[str, Any],
    tools: list[dict[str, Any]] | None = None,
    json_mode: bool = False,
    wire_api: OpenAIWireAPI | None = None,
) -> LLMCompletion:
    """Create one normalized non-streaming completion."""
    selected = _wire_api(wire_api)
    effective_params, _adjustments = _apply_learned_capabilities(client, params, selected)
    if selected == "chat_completions":
        kwargs = _chat_params(effective_params, stream=False)
        kwargs["messages"] = messages
        if tools:
            kwargs["tools"] = tools
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        response = await _await_with_gateway_errors(
            client.chat.completions.create(**kwargs),
            client=client,
            params=effective_params,
            wire_api=selected,
        )
        choice = response.choices[0] if response.choices else None
        message = choice.message if choice is not None else None
        calls = []
        if message is not None:
            for call in getattr(message, "tool_calls", None) or []:
                calls.append(_tool_call(call.id, call.function.name, call.function.arguments))
        return LLMCompletion(
            content=getattr(message, "content", None),
            tool_calls=calls,
            usage=getattr(response, "usage", None),
            model=getattr(response, "model", None),
            finish_reason=getattr(choice, "finish_reason", None),
        )

    kwargs = _responses_params(effective_params, stream=False, json_mode=json_mode)
    kwargs["input"] = messages_to_responses_input(messages)
    response_tools = tools_to_responses(tools)
    if response_tools:
        kwargs["tools"] = response_tools
    response = await _await_with_gateway_errors(
        client.responses.create(**kwargs),
        client=client,
        params=effective_params,
        wire_api=selected,
    )
    if isinstance(response, str):
        return _completion_from_events(_response_fallback_events(response))
    output = _field(response, "output", []) or []
    calls = [
        _tool_call(_field(item, "call_id"), _field(item, "name"), _field(item, "arguments"))
        for item in output
        if _field(item, "type") == "function_call"
    ]
    incomplete_details = _field(response, "incomplete_details")
    incomplete_reason = _field(incomplete_details, "reason")
    return LLMCompletion(
        content=_response_output_text(response),
        tool_calls=calls,
        usage=_field(response, "usage"),
        model=_field(response, "model"),
        status=str(_field(response, "status") or "") or None,
        output_item_types=tuple(
            str(item_type) for item in output if (item_type := _field(item, "type")) is not None
        ),
        incomplete_reason=(str(incomplete_reason) if incomplete_reason is not None else None),
    )


async def _stream_chat_completion(
    client: Any,
    *,
    messages: list[dict[str, Any]],
    params: dict[str, Any],
    tools: list[dict[str, Any]] | None,
) -> AsyncIterator[LLMStreamEvent]:
    """规范化 Chat Completions 的文本、工具与用量增量。"""
    kwargs = _chat_params(params, stream=True)
    kwargs["messages"] = messages
    if tools:
        kwargs["tools"] = tools
    stream = await _await_with_gateway_errors(
        client.chat.completions.create(**kwargs),
        client=client,
        params=params,
        wire_api="chat_completions",
    )
    async for chunk in stream:
        usage = getattr(chunk, "usage", None)
        if usage is not None:
            yield LLMStreamEvent(usage=usage)
        delta = chunk.choices[0].delta if getattr(chunk, "choices", None) else None
        if delta is None:
            continue
        if getattr(delta, "content", None):
            yield LLMStreamEvent(content_delta=delta.content)
        for call in getattr(delta, "tool_calls", None) or []:
            function = getattr(call, "function", None)
            yield LLMStreamEvent(
                tool_call_delta=LLMToolCallDelta(
                    index=int(call.index),
                    id=str(getattr(call, "id", "") or ""),
                    name=str(getattr(function, "name", "") or ""),
                    arguments=str(getattr(function, "arguments", "") or ""),
                )
            )
    yield LLMStreamEvent(completed=True)


def _namespace_value(value: Any) -> Any:
    if isinstance(value, dict):
        return SimpleNamespace(**{str(key): _namespace_value(item) for key, item in value.items()})
    if isinstance(value, list):
        return [_namespace_value(item) for item in value]
    return value


def _raw_sse_response_events(payload: str) -> list[Any]:
    """Parse gateways that return a complete Responses SSE stream as one string."""
    if "data:" not in payload or not any(
        marker in payload
        for marker in ("response.output_text.", "response.output_item.", "response.completed")
    ):
        return []
    parsed: list[Any] = []
    event_name = ""
    data_lines: list[str] = []

    def flush() -> None:
        nonlocal event_name, data_lines
        data = "\n".join(data_lines).strip()
        if data and data != "[DONE]":
            try:
                value = json.loads(data)
            except json.JSONDecodeError:
                value = None
            if isinstance(value, dict):
                if event_name and not value.get("type"):
                    value["type"] = event_name
                parsed.append(_namespace_value(value))
        event_name = ""
        data_lines = []

    for line in payload.replace("\r\n", "\n").split("\n"):
        if not line:
            flush()
        elif line.startswith("event:"):
            event_name = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    flush()
    return parsed


def _completion_from_events(events: list[LLMStreamEvent]) -> LLMCompletion:
    fragments: list[str] = []
    calls: dict[int, dict[str, str]] = {}
    usage: Any | None = None
    status: str | None = None
    output_types: tuple[str, ...] = ()
    incomplete_reason: str | None = None
    model: str | None = None
    for event in events:
        if event.content_delta:
            fragments.append(event.content_delta)
        if event.tool_call_delta is not None:
            delta = event.tool_call_delta
            call = calls.setdefault(delta.index, {"id": "", "name": "", "arguments": ""})
            if delta.id:
                call["id"] = delta.id
            if delta.name:
                call["name"] = delta.name
            if delta.arguments:
                call["arguments"] += delta.arguments
        if event.usage is not None:
            usage = event.usage
        if event.status is not None:
            status = event.status
        if event.output_item_types:
            output_types = event.output_item_types
        if event.incomplete_reason is not None:
            incomplete_reason = event.incomplete_reason
        if event.model is not None:
            model = event.model
    return LLMCompletion(
        content="".join(fragments) or None,
        tool_calls=[
            _tool_call(call["id"], call["name"], call["arguments"] or "{}")
            for _, call in sorted(calls.items())
        ],
        usage=usage,
        model=model,
        status=status,
        output_item_types=output_types,
        incomplete_reason=incomplete_reason,
    )


def _response_fallback_events(response: Any) -> list[LLMStreamEvent]:
    """把不支持异步迭代的 Responses 兼容响应展开为稳定事件。"""
    if isinstance(response, str):
        raw_events = _raw_sse_response_events(response)
        if raw_events:
            state = _ResponseEventState()
            normalized: list[LLMStreamEvent] = []
            for raw_event in raw_events:
                normalized.extend(_normalize_response_stream_event(raw_event, state))
            if normalized:
                return normalized
        text_events = [LLMStreamEvent(content_delta=response)] if response else []
        return [*text_events, LLMStreamEvent(completed=True, status="completed")]
    output = _field(response, "output", []) or []
    events: list[LLMStreamEvent] = []
    fallback_text = _response_output_text(response)
    if fallback_text:
        events.append(LLMStreamEvent(content_delta=fallback_text))
    for index, item in enumerate(output):
        if _field(item, "type") == "function_call":
            events.append(
                LLMStreamEvent(
                    tool_call_delta=LLMToolCallDelta(
                        index=index,
                        id=str(_field(item, "call_id") or ""),
                        name=str(_field(item, "name") or ""),
                        arguments=str(_field(item, "arguments") or "{}"),
                    )
                )
            )
    details = _field(response, "incomplete_details")
    reason = _field(details, "reason")
    events.append(
        LLMStreamEvent(
            usage=_field(response, "usage"),
            completed=True,
            status=str(_field(response, "status") or "completed"),
            output_item_types=tuple(
                str(item_type) for item in output if (item_type := _field(item, "type")) is not None
            ),
            incomplete_reason=str(reason) if reason is not None else None,
            model=(str(_field(response, "model") or "") or None),
        )
    )
    return events


class _ResponseEventState:
    """保存一个 Responses 流的工具索引和文本去重状态。"""

    def __init__(self) -> None:
        self.calls: dict[int, dict[str, str]] = {}
        self.item_indexes: dict[str, int] = {}
        self.text_keys: set[tuple[int, int]] = set()
        self.output_types: list[str] = []


def _response_text_event(event: Any, state: _ResponseEventState) -> list[LLMStreamEvent]:
    """规范化 Responses 文本 delta/done，并避免重复发送最终全文。"""
    key = (
        int(getattr(event, "output_index", 0)),
        int(getattr(event, "content_index", 0)),
    )
    if event.type == "response.output_text.delta":
        state.text_keys.add(key)
        return [LLMStreamEvent(content_delta=str(getattr(event, "delta", "") or ""))]
    text = str(getattr(event, "text", "") or "")
    return [LLMStreamEvent(content_delta=text)] if text and key not in state.text_keys else []


def _response_tool_event(event: Any, state: _ResponseEventState) -> list[LLMStreamEvent]:
    """规范化工具声明、参数增量和最终参数事件。"""
    event_type = str(getattr(event, "type", ""))
    item = getattr(event, "item", None)
    if event_type == "response.output_item.added":
        item_type = str(getattr(item, "type", "") or "")
        if item_type and item_type not in state.output_types:
            state.output_types.append(item_type)
        if item_type != "function_call":
            return []
        index = int(getattr(event, "output_index", len(state.calls)))
        item_id = str(getattr(item, "id", "") or "")
        if item_id:
            state.item_indexes[item_id] = index
        call = state.calls.setdefault(index, {"arguments": ""})
        call.update(
            {
                "id": str(getattr(item, "call_id", "") or ""),
                "name": str(getattr(item, "name", "") or ""),
            }
        )
        delta = LLMToolCallDelta(index=index, id=call.get("id", ""), name=call.get("name", ""))
    elif event_type == "response.function_call_arguments.delta":
        item_id = str(getattr(event, "item_id", "") or "")
        index = int(getattr(event, "output_index", state.item_indexes.get(item_id, 0)))
        arguments = str(getattr(event, "delta", "") or "")
        call = state.calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
        call["arguments"] = call.get("arguments", "") + arguments
        delta = LLMToolCallDelta(index=index, arguments=arguments)
    else:
        if getattr(item, "type", None) != "function_call":
            return []
        index = int(getattr(event, "output_index", 0))
        call = state.calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
        final_arguments = str(getattr(item, "arguments", "") or "")
        delta = LLMToolCallDelta(
            index=index,
            id=str(getattr(item, "call_id", "") or call.get("id", "")),
            name=str(getattr(item, "name", "") or call.get("name", "")),
            arguments=final_arguments if not call.get("arguments") else "",
        )
    return [LLMStreamEvent(tool_call_delta=delta)]


def _response_terminal_event(event: Any, state: _ResponseEventState) -> LLMStreamEvent:
    """规范化 completed/incomplete 终态及用量元数据。"""
    response = getattr(event, "response", None)
    output = getattr(response, "output", []) or []
    output_types = tuple(
        str(item_type) for item in output if (item_type := getattr(item, "type", None)) is not None
    ) or tuple(state.output_types)
    incomplete = event.type == "response.incomplete"
    details = getattr(response, "incomplete_details", None)
    reason = getattr(details, "reason", None)
    return LLMStreamEvent(
        usage=getattr(response, "usage", None),
        completed=True,
        status=(
            "incomplete"
            if incomplete
            else str(getattr(response, "status", "completed") or "completed")
        ),
        output_item_types=output_types,
        incomplete_reason=str(reason) if incomplete and reason is not None else None,
        model=(str(getattr(response, "model", "") or "") or None),
    )


def _normalize_response_stream_event(
    event: Any, state: _ResponseEventState
) -> list[LLMStreamEvent]:
    """分派单个 Responses SDK 事件；未知事件忽略，失败事件抛出。"""
    event_type = str(getattr(event, "type", ""))
    if event_type in {"response.output_text.delta", "response.output_text.done"}:
        return _response_text_event(event, state)
    if event_type in {
        "response.output_item.added",
        "response.function_call_arguments.delta",
        "response.output_item.done",
    }:
        return _response_tool_event(event, state)
    if event_type in {"response.completed", "response.incomplete"}:
        return [_response_terminal_event(event, state)]
    if event_type == "response.failed":
        raise LLMTransportError("LLM Responses stream failed before completion.")
    return []


async def stream_completion(
    client: Any,
    *,
    messages: list[dict[str, Any]],
    params: dict[str, Any],
    tools: list[dict[str, Any]] | None = None,
    json_mode: bool = False,
    wire_api: OpenAIWireAPI | None = None,
) -> AsyncIterator[LLMStreamEvent]:
    """Yield normalized text, tool-call and usage events."""
    selected = _wire_api(wire_api)
    effective_params, _adjustments = _apply_learned_capabilities(client, params, selected)
    if selected == "chat_completions":
        async for event in _stream_chat_completion(
            client,
            messages=messages,
            params=effective_params,
            tools=tools,
        ):
            yield event
        return

    kwargs = _responses_params(effective_params, stream=True, json_mode=json_mode)
    kwargs["input"] = messages_to_responses_input(messages)
    response_tools = tools_to_responses(tools)
    if response_tools:
        kwargs["tools"] = response_tools
    stream = await _await_with_gateway_errors(
        client.responses.create(**kwargs),
        client=client,
        params=effective_params,
        wire_api=selected,
    )
    if not hasattr(stream, "__aiter__"):
        for event in _response_fallback_events(stream):
            yield event
        return
    state = _ResponseEventState()
    async for raw_event in stream:
        for event in _normalize_response_stream_event(raw_event, state):
            yield event


__all__ = [
    "LLMCompletion",
    "LLMFailureInfo",
    "LLMFunctionCall",
    "LLMStreamEvent",
    "LLMToolCall",
    "LLMToolCallDelta",
    "LLMTransportError",
    "create_completion",
    "messages_to_responses_input",
    "stream_completion",
    "tools_to_responses",
]
