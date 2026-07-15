"""llm_json 共享工具单元测试。

验证 JSON 解析、空响应回退和 Mock 客户端路径。
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from miniagent.agent.llm_json import llm_json, parse_llm_json_response
from miniagent.agent.observability import clear_trace_hooks, register_trace_hook


class TestParseLlmJsonResponse:
    """parse_llm_json_response() 行为测试。"""

    def test_direct_json_object(self) -> None:
        data = parse_llm_json_response('{"a": 1}')
        assert data == {"a": 1}

    def test_strips_leading_markdown_fence(self) -> None:
        raw = '```json\n{"ok": true}\n```'
        assert parse_llm_json_response(raw) == {"ok": True}

    def test_strips_plain_fence(self) -> None:
        raw = '```\n{"x": "y"}\n```'
        assert parse_llm_json_response(raw) == {"x": "y"}

    def test_brace_slice_with_surrounding_text(self) -> None:
        raw = '说明文字\n{"summary":"x","steps":[]}\n尾部'
        data = parse_llm_json_response(raw)
        assert data["summary"] == "x"
        assert data["steps"] == []

    def test_fence_mid_text_uses_brace_slice(self) -> None:
        raw = '前缀\n```json\n{"nested": true}\n```\n后缀'
        assert parse_llm_json_response(raw) == {"nested": True}

    def test_strip_fence_disabled(self) -> None:
        raw = '```json\n[1, 2, 3]\n```'
        with pytest.raises(json.JSONDecodeError):
            parse_llm_json_response(raw, strip_fence=False)

    def test_invalid_json_raises(self) -> None:
        with pytest.raises(json.JSONDecodeError):
            parse_llm_json_response("not valid json {{{")

    def test_top_level_array_raises_type_error(self) -> None:
        with pytest.raises(TypeError, match="JSON object"):
            parse_llm_json_response("[1, 2, 3]")

    def test_top_level_scalar_raises_type_error(self) -> None:
        with pytest.raises(TypeError, match="JSON object"):
            parse_llm_json_response('"hello"')


class TestLlmJson:
    """llm_json() 行为测试。"""

    def setup_method(self) -> None:
        clear_trace_hooks()

    def _make_mock_client(self, json_str: str | None) -> MagicMock:
        """构造返回指定 JSON 字符串的 Mock LLM client。"""
        mock_choice = MagicMock()
        mock_choice.message.content = json_str
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]

        async def fake_create(**kw):
            return mock_response

        mock_client = MagicMock()
        mock_client.chat.completions.create = fake_create
        return mock_client

    @pytest.mark.asyncio
    async def test_valid_json_parsed(self) -> None:
        """有效 JSON 应正确解析为字典。"""
        client = self._make_mock_client('{"key": "value", "num": 42}')
        result = await llm_json("test", "system", client=client)
        assert result == {"key": "value", "num": 42}

    @pytest.mark.asyncio
    async def test_json_object_request_mentions_json_in_user_message(self) -> None:
        """json_object requests must also mention json in a user/input message."""
        captured: dict[str, object] = {}
        mock_choice = MagicMock()
        mock_choice.message.content = "{}"
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]

        async def fake_create(**kw):
            captured.update(kw)
            return mock_response

        client = MagicMock()
        client.chat.completions.create = fake_create

        await llm_json("hello", "system", client=client)

        assert captured["response_format"] == {"type": "json_object"}
        messages = captured["messages"]
        assert isinstance(messages, list)
        user_messages = [m for m in messages if m.get("role") == "user"]
        assert any("json" in str(m.get("content", "")).lower() for m in user_messages)
        assert str(user_messages[-1]["content"]).startswith("hello")

    @pytest.mark.asyncio
    async def test_retries_without_json_object_when_unsupported(self) -> None:
        """Unsupported json_object endpoints should fall back to plain completion."""
        calls: list[dict[str, object]] = []
        mock_choice = MagicMock()
        mock_choice.message.content = '{"ok": true}'
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.usage = None

        async def fake_create(**kw):
            calls.append(dict(kw))
            if len(calls) == 1:
                raise TypeError("response_format json_object not supported")
            return mock_response

        client = MagicMock()
        client.chat.completions.create = fake_create

        result = await llm_json("hello", "system", client=client)

        assert result == {"ok": True}
        assert len(calls) == 2
        assert calls[0]["response_format"] == {"type": "json_object"}
        assert "response_format" not in calls[1]

    @pytest.mark.asyncio
    async def test_plain_fallback_failure_still_emits_paired_response(self) -> None:
        events: list[dict] = []
        calls = 0

        async def fake_create(**_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise TypeError("response_format json_object not supported")
            raise RuntimeError("fallback failed")

        client = MagicMock()
        client.chat.completions.create = fake_create
        clear_trace_hooks()
        register_trace_hook(events.append)
        try:
            with pytest.raises(RuntimeError, match="fallback failed"):
                await llm_json(
                    "hello",
                    "system",
                    client=client,
                    trace_phase="test",
                    trace_session_key="session",
                )
        finally:
            clear_trace_hooks()

        requests = [event for event in events if event["type"] == "llm.request"]
        responses = [event for event in events if event["type"] == "llm.response"]
        assert len(requests) == len(responses) == 2
        assert responses[0]["retrying"] is True
        assert responses[1]["retrying"] is False

    @pytest.mark.asyncio
    async def test_parses_markdown_wrapped_json_via_shared_parser(self) -> None:
        """降级或自由格式输出中的 markdown 围栏应被 parse_llm_json_response 剥离。"""
        client = self._make_mock_client('```json\n{"wrapped": true}\n```')
        result = await llm_json("test", "system", client=client)
        assert result == {"wrapped": True}

    @pytest.mark.asyncio
    async def test_invalid_json_returns_empty_dict(self) -> None:
        """无效 JSON 应回退为空字典。"""
        client = self._make_mock_client("not valid json {{{")
        result = await llm_json("test", "system", client=client)
        assert result == {}

    @pytest.mark.asyncio
    async def test_raise_on_error_propagates_json_decode_error(self) -> None:
        client = self._make_mock_client("not valid json {{{")
        with pytest.raises(json.JSONDecodeError):
            await llm_json("test", "system", client=client, raise_on_error=True)

    @pytest.mark.asyncio
    async def test_raise_on_error_propagates_type_error_for_array(self) -> None:
        client = self._make_mock_client("[1, 2]")
        with pytest.raises(TypeError, match="JSON object"):
            await llm_json("test", "system", client=client, raise_on_error=True)

    @pytest.mark.asyncio
    async def test_null_content_returns_empty_dict(self) -> None:
        """空 content 应回退为空字典。"""
        mock_choice = MagicMock()
        mock_choice.message.content = None
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]

        async def fake_create(**kw):
            return mock_response

        client = MagicMock()
        client.chat.completions.create = fake_create
        result = await llm_json("test", "system", client=client)
        assert result == {}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("first_content", [None, "", "not valid json"])
    async def test_retries_empty_or_malformed_content(
        self, first_content: str | None
    ) -> None:
        contents = [first_content, '{"ok": true}']
        calls = 0

        async def fake_create(**_kw):
            nonlocal calls
            content = contents[calls]
            calls += 1
            choice = MagicMock()
            choice.message.content = content
            response = MagicMock()
            response.choices = [choice]
            response.usage = None
            return response

        client = MagicMock()
        client.chat.completions.create = fake_create

        result = await llm_json("test", "system", client=client)

        assert result == {"ok": True}
        assert calls == 2

    @pytest.mark.asyncio
    async def test_raise_on_error_rejects_repeated_empty_content(self) -> None:
        client = self._make_mock_client(None)
        with pytest.raises(ValueError, match="empty text content"):
            await llm_json(
                "test",
                "system",
                client=client,
                raise_on_error=True,
            )

    @pytest.mark.asyncio
    async def test_responses_json_without_explicit_thinking_uses_low(self) -> None:
        async def events():
            yield SimpleNamespace(
                type="response.output_text.delta",
                output_index=0,
                content_index=0,
                delta='{"ok": true}',
            )
            yield SimpleNamespace(
                type="response.completed",
                response=SimpleNamespace(
                    status="completed",
                    output=[SimpleNamespace(type="message")],
                    usage=None,
                    model="response-model",
                ),
            )

        client = MagicMock()
        client.responses.create = AsyncMock(return_value=events())

        with patch("miniagent.llm.legacy_transport._wire_api", return_value="responses"):
            result = await llm_json("test", "system", client=client)

        assert result == {"ok": True}
        assert client.responses.create.await_args.kwargs["reasoning"] == {
            "effort": "low"
        }
        assert client.responses.create.await_args.kwargs["stream"] is True

    @pytest.mark.asyncio
    async def test_responses_retries_reasoning_only_then_parses_stream(self) -> None:
        async def reasoning_only():
            yield SimpleNamespace(
                type="response.output_item.added",
                output_index=0,
                item=SimpleNamespace(type="reasoning"),
            )
            yield SimpleNamespace(
                type="response.completed",
                response=SimpleNamespace(
                    status="completed",
                    output=[SimpleNamespace(type="reasoning")],
                    usage=None,
                    model="response-model",
                ),
            )

        async def valid():
            yield SimpleNamespace(
                type="response.output_text.done",
                output_index=0,
                content_index=0,
                text='{"ok": true}',
            )
            yield SimpleNamespace(
                type="response.completed",
                response=SimpleNamespace(
                    status="completed",
                    output=[SimpleNamespace(type="message")],
                    usage=None,
                    model="response-model",
                ),
            )

        client = MagicMock()
        client.responses.create = AsyncMock(
            side_effect=[reasoning_only(), valid()]
        )
        with (
            patch("miniagent.llm.legacy_transport._wire_api", return_value="responses"),
            patch("miniagent.agent.llm_json.asyncio.sleep", new_callable=AsyncMock),
        ):
            result = await llm_json("test", "system", client=client)

        assert result == {"ok": True}
        assert client.responses.create.await_count == 2

    @pytest.mark.asyncio
    async def test_responses_retries_transient_400_and_uses_low_last(self) -> None:
        class GatewayInvalidRequest(Exception):
            status_code = 400

        async def valid():
            yield SimpleNamespace(
                type="response.output_text.done",
                output_index=0,
                content_index=0,
                text='{"ok": true}',
            )
            yield SimpleNamespace(
                type="response.completed",
                response=SimpleNamespace(
                    status="completed",
                    output=[SimpleNamespace(type="message")],
                    usage=None,
                    model="response-model",
                ),
            )

        client = MagicMock()
        client.responses.create = AsyncMock(
            side_effect=[
                GatewayInvalidRequest("invalid_request_error cch_session_id: probe"),
                GatewayInvalidRequest("invalid_request_error cch_session_id: probe"),
                valid(),
            ]
        )
        with (
            patch("miniagent.llm.legacy_transport._wire_api", return_value="responses"),
            patch("miniagent.agent.llm_json.asyncio.sleep", new_callable=AsyncMock),
        ):
            result = await llm_json("test", "system", client=client)

        assert result == {"ok": True}
        third = client.responses.create.await_args_list[2].kwargs
        assert third["reasoning"] == {"effort": "low"}
        assert "temperature" not in third
        assert "top_p" not in third

    @pytest.mark.asyncio
    async def test_responses_does_not_retry_auth_failure(self) -> None:
        class AuthenticationFailure(Exception):
            status_code = 401

        client = MagicMock()
        client.responses.create = AsyncMock(
            side_effect=AuthenticationFailure("unauthorized")
        )
        with patch("miniagent.llm.legacy_transport._wire_api", return_value="responses"):
            with pytest.raises(AuthenticationFailure):
                await llm_json("test", "system", client=client)

        assert client.responses.create.await_count == 1

    @pytest.mark.asyncio
    async def test_empty_choices_returns_empty_dict(self) -> None:
        mock_response = MagicMock()
        mock_response.choices = []

        async def fake_create(**kw):
            return mock_response

        client = MagicMock()
        client.chat.completions.create = fake_create
        result = await llm_json("test", "system", client=client)
        assert result == {}

    @pytest.mark.asyncio
    async def test_nested_json_preserved(self) -> None:
        """嵌套 JSON 结构应完整保留。"""
        client = self._make_mock_client('{"outer": {"inner": [1, 2, 3]}, "flag": true}')
        result = await llm_json("test", "system", client=client)
        assert result["outer"]["inner"] == [1, 2, 3]
        assert result["flag"] is True

    @pytest.mark.asyncio
    async def test_empty_object_parsed(self) -> None:
        """空对象 {} 应解析为空字典。"""
        client = self._make_mock_client("{}")
        result = await llm_json("test", "system", client=client)
        assert result == {}

    @pytest.mark.asyncio
    async def test_chinese_content(self) -> None:
        """含中文内容的 JSON 应正确解析。"""
        client = self._make_mock_client('{"目标": "获取天气", "城市": ["北京", "上海"]}')
        result = await llm_json("测试", "系统", client=client)
        assert result["目标"] == "获取天气"
        assert "北京" in result["城市"]

    @pytest.mark.asyncio
    async def test_max_tokens_forwarded(self) -> None:
        captured: dict[str, object] = {}
        mock_choice = MagicMock()
        mock_choice.message.content = "{}"
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]

        async def fake_create(**kw):
            captured.update(kw)
            return mock_response

        client = MagicMock()
        client.chat.completions.create = fake_create

        await llm_json("test", "system", client=client, max_tokens=128)
        assert captured["max_tokens"] == 128

    @pytest.mark.asyncio
    async def test_trace_phase_emits_request_and_response(self) -> None:
        events: list[dict] = []
        register_trace_hook(events.append)

        mock_choice = MagicMock()
        mock_choice.message.content = '{"done": true}'
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.usage = None

        async def fake_create(**kw):
            return mock_response

        client = MagicMock()
        client.chat.completions.create = fake_create

        result = await llm_json(
            "test",
            "system",
            client=client,
            trace_phase="reflect",
            trace_session_key="sess-1",
        )

        assert result == {"done": True}
        assert [e["type"] for e in events] == ["llm.request", "llm.response"]
        assert events[0]["phase"] == "reflect"
        assert events[0]["session_key"] == "sess-1"
        assert events[0]["json_object"] is True
        assert events[0]["message_count"] == 2
        assert events[0]["tool_count"] == 0
        assert events[1]["json_object"] is True
        assert events[1]["usage"] is None
        assert isinstance(events[1]["duration_ms"], int)
        assert events[1]["duration_ms"] >= 0

    @pytest.mark.asyncio
    async def test_thinking_level_forwards_model_overrides_extra_body(
        self, tmp_path, monkeypatch
    ) -> None:
        """thinking_level 路径应合并 model_overrides.extra_body。"""
        from tests.config_helpers import install_test_config

        install_test_config(
            tmp_path,
            {
                "model": {
                    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                },
            },
        )

        captured: dict[str, object] = {}
        mock_choice = MagicMock()
        mock_choice.message.content = "{}"
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]

        async def fake_create(**kw):
            captured.update(kw)
            return mock_response

        client = MagicMock()
        client.chat.completions.create = fake_create

        await llm_json(
            "test",
            "system",
            client=client,
            thinking_level="low",
            thinking_budget=512,
            model_overrides={"extra_body": {"custom_field": "value"}},
        )

        extra_body = captured.get("extra_body")
        assert isinstance(extra_body, dict)
        assert extra_body.get("custom_field") == "value"
        assert extra_body.get("enable_thinking") is True
        assert extra_body.get("thinking_budget") == 512
