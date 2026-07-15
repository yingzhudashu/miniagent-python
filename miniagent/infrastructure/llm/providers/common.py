"""Shared provider conversion and error helpers."""

from __future__ import annotations

import json
from typing import Any

from miniagent.contracts.llm import (
    LLMFunctionCall,
    LLMToolCall,
    LLMTransportError,
    LLMUsage,
)


def field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def json_arguments(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value or {}, ensure_ascii=False, separators=(",", ":"))


def tool_call(call_id: Any, name: Any, arguments: Any) -> LLMToolCall:
    raw = json_arguments(arguments)
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        parsed = {}
    return LLMToolCall(
        id=str(call_id or ""),
        function=LLMFunctionCall(str(name or ""), raw),
        _args_dict=parsed if isinstance(parsed, dict) else {},
    )


def usage_from_fields(
    *,
    input_tokens: Any = 0,
    output_tokens: Any = 0,
    cache_read_tokens: Any = 0,
    cache_write_tokens: Any = 0,
    reasoning_tokens: Any = 0,
) -> LLMUsage:
    values = [input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, reasoning_tokens]
    normalized = []
    for value in values:
        try:
            normalized.append(int(value or 0))
        except (TypeError, ValueError):
            normalized.append(0)
    input_count, output_count, cache_read, cache_write, reasoning = normalized
    return LLMUsage(
        input_tokens=input_count,
        output_tokens=output_count,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        reasoning_tokens=reasoning,
        total_tokens=input_count + output_count + cache_read + cache_write,
    )


def normalize_provider_error(error: Exception, provider: str) -> LLMTransportError:
    status_raw = getattr(error, "status_code", None)
    try:
        status = int(status_raw) if status_raw is not None else None
    except (TypeError, ValueError):
        status = None
    name = type(error).__name__.lower()
    message = str(error).lower()
    if status in (401, 403) or "authentication" in name or "api key" in message:
        category = "authentication"
        retryable = False
    elif status == 429 or "rate" in name:
        category = "rate_limit"
        retryable = True
    elif status == 404 or "notfound" in name:
        category = "model_not_found"
        retryable = False
    elif "timeout" in name or "timed out" in message:
        category = "timeout"
        retryable = True
    elif status is not None and status >= 500:
        category = "provider_unavailable"
        retryable = True
    elif "context" in message and ("length" in message or "token" in message):
        category = "context_length"
        retryable = False
    elif status == 400 and ("unsupported" in message or "unknown parameter" in message):
        category = "unsupported_parameter"
        retryable = False
    else:
        category = "unknown"
        retryable = False
    return LLMTransportError(
        f"{provider} request failed ({category})",
        status_code=status,
        category=category,  # type: ignore[arg-type]
        retryable=retryable,
    )


__all__ = [
    "field",
    "json_arguments",
    "normalize_provider_error",
    "tool_call",
    "usage_from_fields",
]
