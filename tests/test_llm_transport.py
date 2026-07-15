"""Protocol-level tests for the Chat/Responses LLM transport."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from miniagent.llm.legacy_transport import (
    LLMTransportError,
    classify_transport_error,
    create_completion,
    create_structured_completion,
    messages_to_responses_input,
    resolve_model_max_output_tokens,
    resolve_wire_api,
    stream_completion,
    tools_to_responses,
)


def test_gateway_role_metadata_overrides_legacy_wire_defaults() -> None:
    class Gateway:
        _miniagent_llm_gateway = True

        @staticmethod
        def model_for_role(role: str) -> SimpleNamespace:
            assert role == "reasoning"
            return SimpleNamespace(api="openai_responses", max_output_tokens=128000)

    gateway = Gateway()
    assert resolve_wire_api(client=gateway, role="reasoning") == "responses"
    assert (
        resolve_model_max_output_tokens(gateway, role="reasoning", fallback=4096)
        == 128000
    )


@pytest.mark.parametrize(
    ("status", "message", "category", "retryable"),
    [
        (400, "invalid_request_error cch_session_id: probe", "transient_api_error", True),
        (401, "unauthorized", "deterministic_api_error", False),
        (403, "permission denied", "deterministic_api_error", False),
        (400, "invalid tool schema", "api_error", False),
        (400, "Unsupported parameter: temperature", "unsupported_parameter", True),
        (500, "upstream error", "transient_api_error", True),
        (None, "connection reset", "network_error", True),
    ],
)
def test_classify_transport_error(
    status: int | None,
    message: str,
    category: str,
    retryable: bool,
) -> None:
    class ProbeError(Exception):
        status_code = status

    failure = classify_transport_error(ProbeError(message))

    assert failure.category == category
    assert failure.retryable is retryable
    assert failure.status_code == status


def test_messages_to_responses_input_preserves_tool_chain_and_image() -> None:
    converted = messages_to_responses_input(
        [
            {"role": "system", "content": "system"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "inspect"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,AA=="},
                    },
                ],
            },
            {
                "role": "assistant",
                "content": "calling",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "ping", "arguments": '{"x":1}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call-1", "content": "pong"},
        ]
    )

    assert converted[0] == {"role": "system", "content": "system"}
    assert converted[1]["content"] == [
        {"type": "input_text", "text": "inspect"},
        {"type": "input_image", "image_url": "data:image/png;base64,AA=="},
    ]
    assert converted[3] == {
        "type": "function_call",
        "call_id": "call-1",
        "name": "ping",
        "arguments": '{"x":1}',
    }
    assert converted[4] == {
        "type": "function_call_output",
        "call_id": "call-1",
        "output": "pong",
    }


def test_tools_to_responses_flattens_function_schema() -> None:
    assert tools_to_responses(
        [
            {
                "type": "function",
                "function": {
                    "name": "ping",
                    "description": "Ping",
                    "parameters": {"type": "object", "properties": {}},
                    "strict": True,
                },
            }
        ]
    ) == [
        {
            "type": "function",
            "name": "ping",
            "description": "Ping",
            "parameters": {"type": "object", "properties": {}},
            "strict": True,
        }
    ]


@pytest.mark.asyncio
async def test_create_completion_chat_preserves_chat_contract() -> None:
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="ok", tool_calls=[]),
                    finish_reason="stop",
                )
            ],
            usage=SimpleNamespace(total_tokens=3),
            model="chat-model",
        )
    )

    result = await create_completion(
        client,
        messages=[{"role": "user", "content": "hi"}],
        params={
            "model": "chat-model",
            "max_tokens": 10,
            "stream": True,
            "_thinking_level": "light",
            "_thinking_budget": 10,
        },
        json_mode=True,
        wire_api="chat_completions",
    )

    assert result.content == "ok"
    assert result.finish_reason == "stop"
    kwargs = client.chat.completions.create.await_args.kwargs
    assert kwargs["messages"] == [{"role": "user", "content": "hi"}]
    assert kwargs["max_tokens"] == 10
    assert kwargs["stream"] is False
    assert kwargs["response_format"] == {"type": "json_object"}
    assert "_thinking_level" not in kwargs


@pytest.mark.asyncio
async def test_create_completion_responses_maps_params_and_normalizes_string() -> None:
    client = MagicMock()
    client.responses.create = AsyncMock(return_value='{"ok":true}')

    result = await create_completion(
        client,
        messages=[{"role": "user", "content": "json"}],
        params={
            "model": "response-model",
            "max_tokens": 20,
            "temperature": 0.0,
            "_thinking_level": "heavy",
            "_thinking_budget": 100,
        },
        json_mode=True,
        wire_api="responses",
    )

    assert result.content == '{"ok":true}'
    kwargs = client.responses.create.await_args.kwargs
    assert kwargs["max_output_tokens"] == 20
    assert kwargs["reasoning"] == {"effort": "high"}
    assert kwargs["stream"] is False
    assert "max_tokens" not in kwargs
    assert "response_format" not in kwargs
    assert kwargs["text"] == {"format": {"type": "json_object"}}


@pytest.mark.asyncio
async def test_responses_parses_gateway_sse_returned_as_one_string() -> None:
    raw_sse = "\n\n".join(
        [
            'event: response.output_item.added\ndata: {"type":"response.output_item.added","output_index":0,"item":{"type":"message","id":"msg-1"}}',
            'event: response.output_text.delta\ndata: {"type":"response.output_text.delta","output_index":0,"content_index":0,"delta":"{\\"ok\\":true}"}',
            'event: response.output_text.done\ndata: {"type":"response.output_text.done","output_index":0,"content_index":0,"text":"{\\"ok\\":true}"}',
            'event: response.completed\ndata: {"type":"response.completed","response":{"status":"completed","model":"response-model","output":[{"type":"message"}],"usage":{"input_tokens":3,"output_tokens":4}}}',
            "data: [DONE]",
        ]
    )
    client = MagicMock()
    client.responses.create = AsyncMock(return_value=raw_sse)

    result = await create_completion(
        client,
        messages=[{"role": "user", "content": "json"}],
        params={"model": "response-model"},
        json_mode=True,
        wire_api="responses",
    )

    assert result.content == '{"ok":true}'
    assert result.status == "completed"
    assert result.output_item_types == ("message",)
    assert result.model == "response-model"


@pytest.mark.asyncio
async def test_responses_stream_parses_gateway_sse_returned_as_one_string() -> None:
    raw_sse = "\n\n".join(
        [
            'event: response.output_text.delta\ndata: {"type":"response.output_text.delta","output_index":0,"content_index":0,"delta":"{\\"ok\\":true}"}',
            'event: response.completed\ndata: {"type":"response.completed","response":{"status":"completed","output":[{"type":"message"}]}}',
        ]
    )
    client = MagicMock()
    client.responses.create = AsyncMock(return_value=raw_sse)

    events = [
        event
        async for event in stream_completion(
            client,
            messages=[{"role": "user", "content": "json"}],
            params={"model": "response-model"},
            json_mode=True,
            wire_api="responses",
        )
    ]

    assert "".join(event.content_delta or "" for event in events) == '{"ok":true}'
    assert events[-1].completed is True
    assert events[-1].status == "completed"


@pytest.mark.asyncio
async def test_responses_json_mode_preserves_explicit_json_schema_format() -> None:
    client = MagicMock()
    client.responses.create = AsyncMock(return_value='{"ok":true}')
    schema_format = {
        "type": "json_schema",
        "name": "answer",
        "schema": {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        },
        "strict": True,
    }

    await create_completion(
        client,
        messages=[{"role": "user", "content": "json"}],
        params={"model": "response-model", "text": {"format": schema_format}},
        json_mode=True,
        wire_api="responses",
    )

    assert client.responses.create.await_args.kwargs["text"] == {"format": schema_format}


@pytest.mark.asyncio
async def test_responses_learns_unsupported_sampling_parameters_per_client() -> None:
    class UnsupportedSamplingError(Exception):
        status_code = 400

    client = MagicMock()
    client.responses.create = AsyncMock(
        side_effect=[
            UnsupportedSamplingError(
                "Unsupported parameters: temperature and top_p are not supported"
            ),
            "ok",
        ]
    )
    params = {
        "model": "learn-model",
        "temperature": 0.7,
        "top_p": 0.9,
    }

    with pytest.raises(UnsupportedSamplingError):
        await create_completion(
            client,
            messages=[{"role": "user", "content": "first"}],
            params=params,
            wire_api="responses",
        )
    result = await create_completion(
        client,
        messages=[{"role": "user", "content": "second"}],
        params=params,
        wire_api="responses",
    )

    assert result.content == "ok"
    first, second = client.responses.create.await_args_list
    assert first.kwargs["temperature"] == 0.7
    assert first.kwargs["top_p"] == 0.9
    assert "temperature" not in second.kwargs
    assert "top_p" not in second.kwargs


@pytest.mark.asyncio
async def test_capability_learning_is_isolated_by_endpoint() -> None:
    class UnsupportedSamplingError(Exception):
        status_code = 400

    client = MagicMock()
    client.base_url = "https://first.example/v1"
    client.responses.create = AsyncMock(
        side_effect=[
            UnsupportedSamplingError("Unsupported parameter: temperature"),
            "ok",
        ]
    )
    params = {"model": "same-model", "temperature": 0.7}

    with pytest.raises(UnsupportedSamplingError):
        await create_completion(client, messages=[], params=params, wire_api="responses")
    client.base_url = "https://second.example/v1"
    await create_completion(client, messages=[], params=params, wire_api="responses")

    assert client.responses.create.await_args_list[1].kwargs["temperature"] == 0.7


@pytest.mark.asyncio
async def test_invalid_sampling_value_does_not_disable_supported_parameter() -> None:
    class InvalidSamplingValue(Exception):
        status_code = 400

    client = MagicMock()
    client.responses.create = AsyncMock(
        side_effect=[
            InvalidSamplingValue("temperature value 3 is not supported; use 0 through 2"),
            "ok",
        ]
    )
    params = {"model": "value-model", "temperature": 3}

    with pytest.raises(InvalidSamplingValue):
        await create_completion(client, messages=[], params=params, wire_api="responses")
    await create_completion(client, messages=[], params=params, wire_api="responses")

    assert client.responses.create.await_args_list[1].kwargs["temperature"] == 3


@pytest.mark.asyncio
async def test_responses_extracts_nested_message_text_and_metadata() -> None:
    usage = SimpleNamespace(output_tokens=12)
    client = MagicMock()
    client.responses.create = AsyncMock(
        return_value=SimpleNamespace(
            output_text="",
            output=[
                SimpleNamespace(type="reasoning"),
                SimpleNamespace(
                    type="message",
                    content=[
                        SimpleNamespace(type="output_text", text='{"ok":true}')
                    ],
                ),
            ],
            status="completed",
            incomplete_details=None,
            usage=usage,
            model="response-model",
        )
    )

    result = await create_completion(
        client,
        messages=[{"role": "user", "content": "json"}],
        params={"model": "response-model"},
        wire_api="responses",
    )

    assert result.content == '{"ok":true}'
    assert result.status == "completed"
    assert result.output_item_types == ("reasoning", "message")
    assert result.incomplete_reason is None
    assert result.usage is usage


@pytest.mark.asyncio
async def test_responses_preserves_reasoning_only_and_incomplete_diagnostics() -> None:
    client = MagicMock()
    client.responses.create = AsyncMock(
        side_effect=[
            SimpleNamespace(
                output_text="",
                output=[SimpleNamespace(type="reasoning")],
                status="completed",
                incomplete_details=None,
                usage=None,
                model="response-model",
            ),
            SimpleNamespace(
                output_text=None,
                output=[SimpleNamespace(type="reasoning")],
                status="incomplete",
                incomplete_details=SimpleNamespace(reason="max_output_tokens"),
                usage=None,
                model="response-model",
            ),
        ]
    )

    reasoning_only = await create_completion(
        client,
        messages=[{"role": "user", "content": "json"}],
        params={"model": "response-model"},
        wire_api="responses",
    )
    incomplete = await create_completion(
        client,
        messages=[{"role": "user", "content": "json"}],
        params={"model": "response-model"},
        wire_api="responses",
    )

    assert reasoning_only.content == ""
    assert reasoning_only.status == "completed"
    assert reasoning_only.output_item_types == ("reasoning",)
    assert incomplete.content is None
    assert incomplete.status == "incomplete"
    assert incomplete.incomplete_reason == "max_output_tokens"


@pytest.mark.asyncio
@pytest.mark.parametrize("thinking_level", [None, "disabled", "none", "off"])
async def test_responses_json_control_requests_force_low_reasoning(
    thinking_level: str | None,
) -> None:
    client = MagicMock()
    client.responses.create = AsyncMock(return_value='{"ok":true}')
    params: dict[str, object] = {"model": "response-model", "max_tokens": 128}
    if thinking_level is not None:
        params["_thinking_level"] = thinking_level

    await create_completion(
        client,
        messages=[{"role": "user", "content": "json"}],
        params=params,
        json_mode=True,
        wire_api="responses",
    )

    assert client.responses.create.await_args.kwargs["reasoning"] == {"effort": "low"}


@pytest.mark.asyncio
async def test_responses_non_json_disabled_reasoning_remains_omitted() -> None:
    client = MagicMock()
    client.responses.create = AsyncMock(return_value="ok")

    await create_completion(
        client,
        messages=[{"role": "user", "content": "plain"}],
        params={"model": "response-model", "_thinking_level": "disabled"},
        wire_api="responses",
    )

    assert "reasoning" not in client.responses.create.await_args.kwargs


@pytest.mark.asyncio
async def test_responses_normalizes_public_thinking_aliases_without_sdk_leak() -> None:
    client = MagicMock()
    client.responses.create = AsyncMock(return_value="ok")

    await create_completion(
        client,
        messages=[{"role": "user", "content": "plan"}],
        params={
            "model": "response-model",
            "thinking_level": "medium",
            "thinking_budget": 81920,
        },
        wire_api="responses",
    )

    kwargs = client.responses.create.await_args.kwargs
    assert kwargs["reasoning"] == {"effort": "medium"}
    assert "thinking_level" not in kwargs
    assert "thinking_budget" not in kwargs


@pytest.mark.asyncio
async def test_stream_completion_chat_normalizes_text_tool_and_usage() -> None:
    usage = SimpleNamespace(total_tokens=4)

    async def chunks():
        yield SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content="hi", tool_calls=[]))],
            usage=None,
        )
        yield SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(
                        content=None,
                        tool_calls=[
                            SimpleNamespace(
                                index=0,
                                id="call-1",
                                function=SimpleNamespace(name="ping", arguments="{}"),
                            )
                        ],
                    )
                )
            ],
            usage=usage,
        )

    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=chunks())
    events = [
        event
        async for event in stream_completion(
            client,
            messages=[{"role": "user", "content": "hi"}],
            params={"model": "m"},
            wire_api="chat_completions",
        )
    ]

    assert any(event.content_delta == "hi" for event in events)
    tool_delta = next(event.tool_call_delta for event in events if event.tool_call_delta)
    assert (tool_delta.id, tool_delta.name, tool_delta.arguments) == (
        "call-1",
        "ping",
        "{}",
    )
    assert any(event.usage is usage for event in events)
    assert events[-1].completed is True


@pytest.mark.asyncio
async def test_stream_completion_responses_normalizes_function_events() -> None:
    usage = SimpleNamespace(total_tokens=5)

    async def events():
        yield SimpleNamespace(
            type="response.output_item.added",
            output_index=0,
            item=SimpleNamespace(
                type="function_call", id="item-1", call_id="call-1", name="ping"
            ),
        )
        yield SimpleNamespace(
            type="response.function_call_arguments.delta",
            output_index=0,
            item_id="item-1",
            delta='{"x":',
        )
        yield SimpleNamespace(
            type="response.function_call_arguments.delta",
            output_index=0,
            item_id="item-1",
            delta="1}",
        )
        yield SimpleNamespace(
            type="response.output_item.done",
            output_index=0,
            item=SimpleNamespace(
                type="function_call",
                call_id="call-1",
                name="ping",
                arguments='{"x":1}',
            ),
        )
        yield SimpleNamespace(type="response.output_text.delta", delta="done")
        yield SimpleNamespace(
            type="response.completed", response=SimpleNamespace(usage=usage)
        )

    client = MagicMock()
    client.responses.create = AsyncMock(return_value=events())
    normalized = [
        event
        async for event in stream_completion(
            client,
            messages=[{"role": "user", "content": "use tool"}],
            params={"model": "m", "max_tokens": 10},
            wire_api="responses",
        )
    ]

    tool_events = [event.tool_call_delta for event in normalized if event.tool_call_delta]
    assert tool_events[0].id == "call-1"
    assert "".join(event.arguments for event in tool_events) == '{"x":1}'
    assert any(event.content_delta == "done" for event in normalized)
    assert normalized[-1].usage is usage
    assert normalized[-1].completed is True


@pytest.mark.asyncio
@pytest.mark.parametrize("include_delta", [False, True])
async def test_stream_completion_responses_uses_done_text_without_duplicates(
    include_delta: bool,
) -> None:
    async def events():
        if include_delta:
            yield SimpleNamespace(
                type="response.output_text.delta",
                output_index=0,
                content_index=0,
                delta="done-only",
            )
        yield SimpleNamespace(
            type="response.output_text.done",
            output_index=0,
            content_index=0,
            text="done-only",
        )
        yield SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(usage=None),
        )

    client = MagicMock()
    client.responses.create = AsyncMock(return_value=events())
    normalized = [
        event
        async for event in stream_completion(
            client,
            messages=[{"role": "user", "content": "hi"}],
            params={"model": "m"},
            wire_api="responses",
        )
    ]

    assert "".join(event.content_delta or "" for event in normalized) == "done-only"
    assert normalized[-1].completed is True


@pytest.mark.asyncio
async def test_structured_completion_responses_streams_and_preserves_metadata() -> None:
    usage = SimpleNamespace(output_tokens=9)

    async def events():
        yield SimpleNamespace(
            type="response.output_item.added",
            output_index=0,
            item=SimpleNamespace(type="reasoning", id="reasoning-1"),
        )
        yield SimpleNamespace(
            type="response.output_text.done",
            output_index=1,
            content_index=0,
            text='{"ok":true}',
        )
        yield SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(
                status="completed",
                output=[
                    SimpleNamespace(type="reasoning"),
                    SimpleNamespace(type="message"),
                ],
                usage=usage,
                model="response-model",
            ),
        )

    client = MagicMock()
    client.responses.create = AsyncMock(return_value=events())
    result = await create_structured_completion(
        client,
        messages=[{"role": "user", "content": "json"}],
        params={"model": "response-model", "_thinking_level": "disabled"},
        wire_api="responses",
    )

    assert result.content == '{"ok":true}'
    assert result.status == "completed"
    assert result.output_item_types == ("reasoning", "message")
    assert result.usage is usage
    kwargs = client.responses.create.await_args.kwargs
    assert kwargs["stream"] is True
    assert kwargs["reasoning"] == {"effort": "low"}


@pytest.mark.asyncio
async def test_structured_completion_chat_keeps_json_object_contract() -> None:
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content='{"ok":true}', tool_calls=[]),
                    finish_reason="stop",
                )
            ],
            usage=None,
            model="chat-model",
        )
    )

    result = await create_structured_completion(
        client,
        messages=[{"role": "user", "content": "json"}],
        params={"model": "chat-model"},
        wire_api="chat_completions",
    )

    assert result.content == '{"ok":true}'
    kwargs = client.chat.completions.create.await_args.kwargs
    assert kwargs["stream"] is False
    assert kwargs["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_structured_completion_preserves_incomplete_stream_metadata() -> None:
    async def events():
        yield SimpleNamespace(
            type="response.incomplete",
            response=SimpleNamespace(
                output=[SimpleNamespace(type="reasoning")],
                incomplete_details=SimpleNamespace(reason="max_output_tokens"),
                usage=None,
                model="response-model",
            ),
        )

    client = MagicMock()
    client.responses.create = AsyncMock(return_value=events())
    result = await create_structured_completion(
        client,
        messages=[{"role": "user", "content": "json"}],
        params={"model": "response-model"},
        wire_api="responses",
    )

    assert result.content is None
    assert result.status == "incomplete"
    assert result.output_item_types == ("reasoning",)
    assert result.incomplete_reason == "max_output_tokens"


@pytest.mark.asyncio
async def test_structured_completion_failed_stream_is_sanitized() -> None:
    async def events():
        yield SimpleNamespace(
            type="response.failed",
            response=SimpleNamespace(error=SimpleNamespace(message="secret raw error")),
        )

    client = MagicMock()
    client.responses.create = AsyncMock(return_value=events())
    with pytest.raises(LLMTransportError, match="failed before completion") as caught:
        await create_structured_completion(
            client,
            messages=[{"role": "user", "content": "json"}],
            params={"model": "response-model"},
            wire_api="responses",
        )

    assert "secret raw error" not in str(caught.value)


@pytest.mark.asyncio
async def test_cloudflare_error_is_sanitized() -> None:
    class CloudflareError(Exception):
        status_code = 403

    client = MagicMock()
    client.responses.create = AsyncMock(
        side_effect=CloudflareError("Attention Required! | Cloudflare <html>...")
    )

    with pytest.raises(LLMTransportError, match="Cloudflare/WAF"):
        await create_completion(
            client,
            messages=[{"role": "user", "content": "hi"}],
            params={"model": "m"},
            wire_api="responses",
        )
