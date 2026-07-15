"""Offline message/tool conversion tests for native provider adapters."""

from __future__ import annotations

from miniagent.infrastructure.llm.providers.anthropic import (
    _anthropic_messages,
    _anthropic_tools,
)
from miniagent.infrastructure.llm.providers.common import normalize_provider_error
from miniagent.infrastructure.llm.providers.google import _google_contents, _google_tools


def _conversation():
    return [
        {"role": "system", "content": "be helpful"},
        {"role": "user", "content": "weather"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "weather", "arguments": '{"city":"杭州"}'},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-1",
            "name": "weather",
            "content": "sunny",
        },
    ]


def _tools():
    return [
        {
            "type": "function",
            "function": {
                "name": "weather",
                "description": "lookup",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                },
            },
        }
    ]


def test_anthropic_conversion_preserves_tool_chain() -> None:
    system, messages = _anthropic_messages(_conversation())
    assert system == "be helpful"
    assert messages[1]["content"][0]["type"] == "tool_use"
    assert messages[2]["content"][0]["type"] == "tool_result"
    assert _anthropic_tools(_tools())[0]["input_schema"]["type"] == "object"


def test_google_conversion_preserves_tool_chain() -> None:
    system, contents = _google_contents(_conversation())
    assert system == "be helpful"
    assert contents[1]["role"] == "model"
    assert contents[1]["parts"][0]["function_call"]["name"] == "weather"
    assert contents[2]["parts"][0]["function_response"]["name"] == "weather"
    assert _google_tools(_tools())[0]["function_declarations"][0]["name"] == "weather"


def test_provider_errors_are_sanitized_and_classified() -> None:
    class SecretRateLimitError(Exception):
        status_code = 429

    error = normalize_provider_error(
        SecretRateLimitError("secret response body"), "native"
    )
    assert error.category == "rate_limit"
    assert error.retryable is True
    assert "secret response body" not in str(error)
