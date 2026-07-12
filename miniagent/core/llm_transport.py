"""Protocol-neutral OpenAI transport for Chat Completions and Responses."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from miniagent.core.config import get_default_model_config
from miniagent.types.config import WireAPI


@dataclass(slots=True)
class LLMFunctionCall:
    name: str
    arguments: str


@dataclass(slots=True)
class LLMToolCall:
    id: str
    function: LLMFunctionCall
    _args_dict: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LLMCompletion:
    content: str | None
    tool_calls: list[LLMToolCall] = field(default_factory=list)
    usage: Any | None = None
    model: str | None = None
    status: str | None = None
    output_item_types: tuple[str, ...] = ()
    incomplete_reason: str | None = None
    finish_reason: str | None = None


@dataclass(slots=True)
class LLMToolCallDelta:
    index: int
    id: str = ""
    name: str = ""
    arguments: str = ""


@dataclass(slots=True)
class LLMStreamEvent:
    content_delta: str | None = None
    tool_call_delta: LLMToolCallDelta | None = None
    usage: Any | None = None
    completed: bool = False
    status: str | None = None
    output_item_types: tuple[str, ...] = ()
    incomplete_reason: str | None = None
    model: str | None = None


@dataclass(frozen=True, slots=True)
class LLMFailureInfo:
    category: str
    retryable: bool
    status_code: int | None = None


class LLMTransportError(RuntimeError):
    """Sanitized error raised for recognized gateway failures."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _wire_api(override: WireAPI | None) -> WireAPI:
    return override or get_default_model_config().wire_api


def resolve_wire_api(override: WireAPI | None = None) -> WireAPI:
    """Return the effective wire protocol used by transport calls."""
    return _wire_api(override)


def _normalize_gateway_error(exc: Exception) -> LLMTransportError | None:
    message = str(exc)
    status = getattr(exc, "status_code", None)
    lowered = message.lower()
    if status == 403 and ("cloudflare" in lowered or "attention required" in lowered):
        return LLMTransportError(
            "LLM endpoint rejected the SDK client at its Cloudflare/WAF layer (HTTP 403). "
            "Configure model.user_agent with a value accepted by the endpoint."
        )
    if "no_available_providers" in lowered:
        return LLMTransportError(
            "LLM endpoint has no provider available for this model/client "
            "(no_available_providers). Check model.wire_api, model.model and gateway access."
        )
    return None


def classify_transport_error(error: Exception) -> LLMFailureInfo:
    """Classify one API/transport error without exposing its raw payload."""
    status_raw = getattr(error, "status_code", None)
    try:
        status = int(status_raw) if status_raw is not None else None
    except (TypeError, ValueError):
        status = None
    message = str(error).lower()
    deterministic_markers = (
        "invalid api key",
        "incorrect api key",
        "authentication",
        "permission denied",
        "model_not_found",
        "model does not exist",
        "no_available_providers",
        "cloudflare/waf",
        "http 403",
    )
    if status in (401, 403) or any(
        marker in message for marker in deterministic_markers
    ):
        return LLMFailureInfo("deterministic_api_error", False, status)
    if status == 404 and ("model" in message or "not found" in message):
        return LLMFailureInfo("deterministic_api_error", False, status)
    generic_invalid_request = status == 400 and (
        "invalid_request_error" in message
        or "cch_session_id" in message
        or "上游请求参数无效" in message
    )
    if generic_invalid_request or status == 429 or bool(status and status >= 500):
        return LLMFailureInfo("transient_api_error", True, status)
    if status is None:
        return LLMFailureInfo("network_error", True, None)
    return LLMFailureInfo("api_error", False, status)


def completion_failure_category(completion: LLMCompletion) -> str | None:
    """Classify an empty normalized completion; non-empty text returns ``None``."""
    if (completion.content or "").strip():
        return None
    output_types = set(completion.output_item_types)
    if completion.status == "incomplete":
        return "incomplete_output"
    if completion.status == "failed":
        return "failed_response"
    if output_types and output_types <= {"reasoning"}:
        return "reasoning_only"
    if completion.status == "completed":
        return "completed_without_text"
    return "empty_gateway_response"


def structured_retry_params(
    current: dict[str, Any],
    *,
    next_attempt: int,
    max_attempts: int,
    final_reasoning: str,
    model_max_tokens: int,
    incomplete_reason: str | None = None,
) -> dict[str, Any]:
    """Adapt a Responses structured retry while preserving its first request."""
    recovered = dict(current)
    recovered.pop("temperature", None)
    recovered.pop("top_p", None)
    if next_attempt == max_attempts:
        recovered["_thinking_level"] = final_reasoning
    normalized_reason = str(incomplete_reason or "").strip().lower()
    if any(
        marker in normalized_reason
        for marker in ("max_output_tokens", "max_tokens", "token_limit", "length")
    ):
        current_budget = int(recovered.get("max_tokens", 0) or 0)
        if current_budget > 0 and model_max_tokens > current_budget:
            recovered["max_tokens"] = min(current_budget * 2, model_max_tokens)
    return recovered


def structured_retry_delay(next_attempt: int) -> float:
    """Return the bounded delay before a 1-based structured retry attempt."""
    return 0.2 if next_attempt == 2 else 0.5


async def _await_with_gateway_errors(call: Any) -> Any:
    try:
        return await call
    except Exception as exc:
        normalized = _normalize_gateway_error(exc)
        if normalized is not None:
            raise normalized from None
        raise


def _chat_params(params: dict[str, Any], *, stream: bool) -> dict[str, Any]:
    result = dict(params)
    result["stream"] = stream
    result.pop("_thinking_level", None)
    result.pop("_thinking_budget", None)
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
    source.pop("_thinking_budget", None)
    if "max_tokens" in source:
        source["max_output_tokens"] = source.pop("max_tokens")
    effort = _reasoning_effort(thinking_level, json_mode=json_mode)
    if effort:
        source["reasoning"] = {"effort": effort}
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
    wire_api: WireAPI | None = None,
) -> LLMCompletion:
    """Create one normalized non-streaming completion."""
    selected = _wire_api(wire_api)
    if selected == "chat_completions":
        kwargs = _chat_params(params, stream=False)
        kwargs["messages"] = messages
        if tools:
            kwargs["tools"] = tools
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        response = await _await_with_gateway_errors(
            client.chat.completions.create(**kwargs)
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

    kwargs = _responses_params(params, stream=False, json_mode=json_mode)
    kwargs["input"] = messages_to_responses_input(messages)
    response_tools = tools_to_responses(tools)
    if response_tools:
        kwargs["tools"] = response_tools
    response = await _await_with_gateway_errors(client.responses.create(**kwargs))
    if isinstance(response, str):
        return LLMCompletion(content=response)
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
            str(item_type)
            for item in output
            if (item_type := _field(item, "type")) is not None
        ),
        incomplete_reason=(
            str(incomplete_reason) if incomplete_reason is not None else None
        ),
    )


async def stream_completion(
    client: Any,
    *,
    messages: list[dict[str, Any]],
    params: dict[str, Any],
    tools: list[dict[str, Any]] | None = None,
    json_mode: bool = False,
    wire_api: WireAPI | None = None,
) -> AsyncIterator[LLMStreamEvent]:
    """Yield normalized text, tool-call and usage events."""
    selected = _wire_api(wire_api)
    if selected == "chat_completions":
        kwargs = _chat_params(params, stream=True)
        kwargs["messages"] = messages
        if tools:
            kwargs["tools"] = tools
        stream = await _await_with_gateway_errors(client.chat.completions.create(**kwargs))
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
        return

    kwargs = _responses_params(params, stream=True, json_mode=json_mode)
    kwargs["input"] = messages_to_responses_input(messages)
    response_tools = tools_to_responses(tools)
    if response_tools:
        kwargs["tools"] = response_tools
    stream = await _await_with_gateway_errors(client.responses.create(**kwargs))
    if not hasattr(stream, "__aiter__"):
        if isinstance(stream, str):
            if stream:
                yield LLMStreamEvent(content_delta=stream)
            yield LLMStreamEvent(completed=True, status="completed")
            return
        output = _field(stream, "output", []) or []
        fallback_text = _response_output_text(stream)
        if fallback_text:
            yield LLMStreamEvent(content_delta=fallback_text)
        for index, item in enumerate(output):
            if _field(item, "type") != "function_call":
                continue
            yield LLMStreamEvent(
                tool_call_delta=LLMToolCallDelta(
                    index=index,
                    id=str(_field(item, "call_id") or ""),
                    name=str(_field(item, "name") or ""),
                    arguments=str(_field(item, "arguments") or "{}"),
                )
            )
        details = _field(stream, "incomplete_details")
        reason = _field(details, "reason")
        yield LLMStreamEvent(
            usage=_field(stream, "usage"),
            completed=True,
            status=str(_field(stream, "status") or "completed"),
            output_item_types=tuple(
                str(item_type)
                for item in output
                if (item_type := _field(item, "type")) is not None
            ),
            incomplete_reason=str(reason) if reason is not None else None,
            model=(str(_field(stream, "model") or "") or None),
        )
        return
    call_state: dict[int, dict[str, str]] = {}
    item_indexes: dict[str, int] = {}
    text_delta_keys: set[tuple[int, int]] = set()
    output_item_types: list[str] = []
    async for event in stream:
        event_type = str(getattr(event, "type", ""))
        if event_type == "response.output_text.delta":
            text_delta_keys.add(
                (
                    int(getattr(event, "output_index", 0)),
                    int(getattr(event, "content_index", 0)),
                )
            )
            yield LLMStreamEvent(content_delta=str(getattr(event, "delta", "") or ""))
        elif event_type == "response.output_text.done":
            text_key = (
                int(getattr(event, "output_index", 0)),
                int(getattr(event, "content_index", 0)),
            )
            final_text = str(getattr(event, "text", "") or "")
            if final_text and text_key not in text_delta_keys:
                yield LLMStreamEvent(content_delta=final_text)
        elif event_type == "response.output_item.added":
            item = getattr(event, "item", None)
            item_type = str(getattr(item, "type", "") or "")
            if item_type and item_type not in output_item_types:
                output_item_types.append(item_type)
            if item_type == "function_call":
                index = int(getattr(event, "output_index", len(call_state)))
                item_id = str(getattr(item, "id", "") or "")
                if item_id:
                    item_indexes[item_id] = index
                state = call_state.setdefault(index, {"arguments": ""})
                state.update(
                    {
                        "id": str(getattr(item, "call_id", "") or ""),
                        "name": str(getattr(item, "name", "") or ""),
                    }
                )
                yield LLMStreamEvent(
                    tool_call_delta=LLMToolCallDelta(
                        index=index,
                        id=state.get("id", ""),
                        name=state.get("name", ""),
                    )
                )
        elif event_type == "response.function_call_arguments.delta":
            item_id = str(getattr(event, "item_id", "") or "")
            index = int(
                getattr(event, "output_index", item_indexes.get(item_id, 0))
            )
            arguments = str(getattr(event, "delta", "") or "")
            state = call_state.setdefault(index, {"id": "", "name": "", "arguments": ""})
            state["arguments"] = state.get("arguments", "") + arguments
            yield LLMStreamEvent(
                tool_call_delta=LLMToolCallDelta(index=index, arguments=arguments)
            )
        elif event_type == "response.output_item.done":
            item = getattr(event, "item", None)
            if getattr(item, "type", None) == "function_call":
                index = int(getattr(event, "output_index", 0))
                state = call_state.setdefault(
                    index, {"id": "", "name": "", "arguments": ""}
                )
                final_arguments = str(getattr(item, "arguments", "") or "")
                arguments_delta = final_arguments if not state.get("arguments") else ""
                yield LLMStreamEvent(
                    tool_call_delta=LLMToolCallDelta(
                        index=index,
                        id=str(getattr(item, "call_id", "") or state.get("id", "")),
                        name=str(getattr(item, "name", "") or state.get("name", "")),
                        arguments=arguments_delta,
                    )
                )
        elif event_type == "response.completed":
            response = getattr(event, "response", None)
            final_output = getattr(response, "output", []) or []
            final_types = tuple(
                str(final_item_type)
                for item in final_output
                if (final_item_type := getattr(item, "type", None)) is not None
            ) or tuple(output_item_types)
            yield LLMStreamEvent(
                usage=getattr(response, "usage", None),
                completed=True,
                status=str(getattr(response, "status", "completed") or "completed"),
                output_item_types=final_types,
                model=(str(getattr(response, "model", "") or "") or None),
            )
        elif event_type == "response.incomplete":
            response = getattr(event, "response", None)
            details = getattr(response, "incomplete_details", None)
            reason = getattr(details, "reason", None)
            final_output = getattr(response, "output", []) or []
            yield LLMStreamEvent(
                usage=getattr(response, "usage", None),
                completed=True,
                status="incomplete",
                output_item_types=tuple(
                    str(final_item_type)
                    for item in final_output
                    if (final_item_type := getattr(item, "type", None)) is not None
                )
                or tuple(output_item_types),
                incomplete_reason=str(reason) if reason is not None else None,
                model=(str(getattr(response, "model", "") or "") or None),
            )
        elif event_type == "response.failed":
            raise LLMTransportError("LLM Responses stream failed before completion.")


async def create_structured_completion(
    client: Any,
    *,
    messages: list[dict[str, Any]],
    params: dict[str, Any],
    wire_api: WireAPI | None = None,
) -> LLMCompletion:
    """Create a JSON-oriented completion using the stable Responses stream path."""
    selected = _wire_api(wire_api)
    if selected == "chat_completions":
        return await create_completion(
            client,
            messages=messages,
            params=params,
            json_mode=True,
            wire_api=selected,
        )

    fragments: list[str] = []
    call_state: dict[int, dict[str, str]] = {}
    usage: Any | None = None
    status: str | None = None
    output_item_types: tuple[str, ...] = ()
    incomplete_reason: str | None = None
    model: str | None = None
    async for event in stream_completion(
        client,
        messages=messages,
        params=params,
        json_mode=True,
        wire_api=selected,
    ):
        if event.content_delta:
            fragments.append(event.content_delta)
        if event.usage is not None:
            usage = event.usage
        if event.status is not None:
            status = event.status
        if event.output_item_types:
            output_item_types = event.output_item_types
        if event.incomplete_reason is not None:
            incomplete_reason = event.incomplete_reason
        if event.model is not None:
            model = event.model
        delta = event.tool_call_delta
        if delta is not None:
            state = call_state.setdefault(
                delta.index,
                {"id": "", "name": "", "arguments": ""},
            )
            if delta.id:
                state["id"] = delta.id
            if delta.name:
                state["name"] = delta.name
            if delta.arguments:
                state["arguments"] += delta.arguments

    calls = [
        _tool_call(state["id"], state["name"], state["arguments"])
        for _, state in sorted(call_state.items())
    ]
    content = "".join(fragments)
    return LLMCompletion(
        content=content or None,
        tool_calls=calls,
        usage=usage,
        model=model,
        status=status,
        output_item_types=output_item_types,
        incomplete_reason=incomplete_reason,
    )


__all__ = [
    "LLMCompletion",
    "LLMFailureInfo",
    "LLMFunctionCall",
    "LLMStreamEvent",
    "LLMToolCall",
    "LLMToolCallDelta",
    "LLMTransportError",
    "classify_transport_error",
    "completion_failure_category",
    "create_completion",
    "create_structured_completion",
    "messages_to_responses_input",
    "resolve_wire_api",
    "stream_completion",
    "structured_retry_delay",
    "structured_retry_params",
    "tools_to_responses",
]
