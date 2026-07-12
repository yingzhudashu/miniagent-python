"""任务难度分类：映射函数与 JSON 解析（无网络）。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from miniagent.core._openai_compat import json_object_unsupported
from miniagent.core.task_classifier import (
    TaskDifficulty,
    classify_task_difficulty,
    default_step_thinking_for_difficulty,
    exec_merge_for_simple_path,
    planner_merge_for_difficulty,
    task_classifier_enabled,
)
from miniagent.core.thinking_presets import THINKING_LEVEL_PRESETS
from tests.memory_helpers import make_knowledge_registry


def test_task_classifier_enabled_reads_internal_constant() -> None:
    with patch("miniagent.core.constants.EXECUTION_TASK_CLASSIFIER_ENABLED", True):
        assert task_classifier_enabled() is True
    with patch("miniagent.core.constants.EXECUTION_TASK_CLASSIFIER_ENABLED", False):
        assert task_classifier_enabled() is False


def test_planner_merge_scales_with_difficulty() -> None:
    low = planner_merge_for_difficulty(TaskDifficulty.NORMAL)
    mid = planner_merge_for_difficulty(TaskDifficulty.MEDIUM)
    high = planner_merge_for_difficulty(TaskDifficulty.COMPLEX)
    assert low["thinking_budget"] < high["thinking_budget"]
    assert mid["thinking_budget"] <= high["thinking_budget"]


def test_planner_merge_simple_matches_normal() -> None:
    normal = planner_merge_for_difficulty(TaskDifficulty.NORMAL)
    simple = planner_merge_for_difficulty(TaskDifficulty.SIMPLE)
    assert simple == normal


def test_default_step_thinking_mapping() -> None:
    assert default_step_thinking_for_difficulty(TaskDifficulty.NORMAL) == "low"
    assert default_step_thinking_for_difficulty(TaskDifficulty.SIMPLE) == "low"
    assert default_step_thinking_for_difficulty(TaskDifficulty.MEDIUM) == "medium"
    assert default_step_thinking_for_difficulty(TaskDifficulty.COMPLEX) == "high"


def test_exec_merge_simple_path_matches_low_preset() -> None:
    m = exec_merge_for_simple_path()
    tl, tb = THINKING_LEVEL_PRESETS["low"]
    assert m == {"thinking_level": tl, "thinking_budget": tb}
    assert m == planner_merge_for_difficulty(TaskDifficulty.NORMAL)


def test_json_object_unsupported_ignores_missing_json_keyword() -> None:
    err = Exception(
        "Response input messages must contain the word 'json' in some form "
        "to use 'response.format' of type 'json_object'."
    )
    assert not json_object_unsupported(err)


def _mock_client(content: str) -> MagicMock:
    ok = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=None,
    )
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=ok)
    return client


@pytest.mark.asyncio
async def test_classifier_retries_without_json_object() -> None:
    ok = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"difficulty":"medium"}'))]
    )
    n = 0

    async def _create(**_kwargs: object) -> SimpleNamespace:
        nonlocal n
        n += 1
        if n == 1:
            raise TypeError("response_format json_object not supported")
        return ok

    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=_create)

    d = await classify_task_difficulty(
        "hello",
        ["tb1"],
        knowledge_registry=make_knowledge_registry(),
        client=client,
        agent_config=None,
    )
    assert d == TaskDifficulty.MEDIUM
    assert n == 2


@pytest.mark.asyncio
async def test_classifier_json_object_user_message_mentions_json() -> None:
    captured: dict[str, object] = {}
    ok = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"difficulty":"normal"}'))],
        usage=None,
    )

    async def _create(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return ok

    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=_create)

    d = await classify_task_difficulty(
        "hello",
        ["tb1"],
        knowledge_registry=make_knowledge_registry(),
        client=client,
        agent_config=None,
    )

    assert d == TaskDifficulty.NORMAL
    assert captured["response_format"] == {"type": "json_object"}
    messages = captured["messages"]
    assert isinstance(messages, list)
    user_messages = [m for m in messages if m.get("role") == "user"]
    assert any("json" in str(m.get("content", "")).lower() for m in user_messages)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ('{"difficulty":"simple"}', TaskDifficulty.SIMPLE),
        ('{"difficulty":"复杂"}', TaskDifficulty.COMPLEX),
        ('{"difficulty":"中等"}', TaskDifficulty.MEDIUM),
        ('{"difficulty":"一般"}', TaskDifficulty.NORMAL),
    ],
)
async def test_classifier_difficulty_parsing(payload: str, expected: TaskDifficulty) -> None:
    d = await classify_task_difficulty(
        "hello",
        ["tb1"],
        knowledge_registry=make_knowledge_registry(),
        client=_mock_client(payload),
    )
    assert d == expected


@pytest.mark.asyncio
async def test_classifier_unknown_difficulty_fallback() -> None:
    client = _mock_client('{"difficulty":"unknown"}')
    d = await classify_task_difficulty(
        "hello",
        ["tb1"],
        knowledge_registry=make_knowledge_registry(),
        client=client,
    )
    assert d == TaskDifficulty.NORMAL
    assert client.chat.completions.create.await_count == 1


@pytest.mark.asyncio
async def test_classifier_malformed_json_fallback() -> None:
    client = _mock_client("not json at all")
    d = await classify_task_difficulty(
        "hello",
        ["tb1"],
        knowledge_registry=make_knowledge_registry(),
        client=client,
    )
    assert d == TaskDifficulty.NORMAL
    assert client.chat.completions.create.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("first_content", [None, "", "not json"])
async def test_classifier_retries_empty_or_malformed_response(
    first_content: str | None,
) -> None:
    responses = [
        SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=first_content))],
            usage=None,
        ),
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content='{"difficulty":"medium"}')
                )
            ],
            usage=None,
        ),
    ]
    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=responses)

    result = await classify_task_difficulty(
        "hello",
        ["tb1"],
        knowledge_registry=make_knowledge_registry(),
        client=client,
    )

    assert result == TaskDifficulty.MEDIUM
    assert client.chat.completions.create.await_count == 2


@pytest.mark.asyncio
async def test_classifier_responses_json_uses_low_reasoning() -> None:
    async def events():
        yield SimpleNamespace(
            type="response.output_text.done",
            output_index=0,
            content_index=0,
            text='{"difficulty":"simple"}',
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

    with patch("miniagent.core.llm_transport._wire_api", return_value="responses"):
        result = await classify_task_difficulty(
            "hello",
            ["tb1"],
            knowledge_registry=make_knowledge_registry(),
            client=client,
        )

    assert result == TaskDifficulty.SIMPLE
    assert client.responses.create.await_args.kwargs["reasoning"] == {"effort": "low"}
    assert client.responses.create.await_args.kwargs["stream"] is True


@pytest.mark.asyncio
async def test_classifier_responses_recovers_reasoning_only_stream() -> None:
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
            text='{"difficulty":"complex"}',
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
        patch("miniagent.core.llm_transport._wire_api", return_value="responses"),
        patch("miniagent.core.task_classifier.asyncio.sleep", new_callable=AsyncMock),
    ):
        result = await classify_task_difficulty(
            "complex task",
            ["tb1"],
            knowledge_registry=make_knowledge_registry(),
            client=client,
        )

    assert result == TaskDifficulty.COMPLEX
    assert client.responses.create.await_count == 2
    assert "temperature" not in client.responses.create.await_args_list[1].kwargs
    assert "top_p" not in client.responses.create.await_args_list[1].kwargs


@pytest.mark.asyncio
async def test_classifier_responses_third_attempt_uses_low() -> None:
    class GatewayInvalidRequest(Exception):
        status_code = 400

    async def valid():
        yield SimpleNamespace(
            type="response.output_text.done",
            output_index=0,
            content_index=0,
            text='{"difficulty":"medium"}',
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
        patch("miniagent.core.llm_transport._wire_api", return_value="responses"),
        patch("miniagent.core.task_classifier.asyncio.sleep", new_callable=AsyncMock),
    ):
        result = await classify_task_difficulty(
            "medium task",
            ["tb1"],
            knowledge_registry=make_knowledge_registry(),
            client=client,
        )

    assert result == TaskDifficulty.MEDIUM
    assert client.responses.create.await_args_list[2].kwargs["reasoning"] == {
        "effort": "low"
    }


@pytest.mark.asyncio
async def test_classifier_responses_does_not_retry_auth_failure() -> None:
    class AuthenticationFailure(Exception):
        status_code = 401

    client = MagicMock()
    client.responses.create = AsyncMock(side_effect=AuthenticationFailure("unauthorized"))
    with patch("miniagent.core.llm_transport._wire_api", return_value="responses"):
        result = await classify_task_difficulty(
            "task",
            ["tb1"],
            knowledge_registry=make_knowledge_registry(),
            client=client,
        )

    assert result == TaskDifficulty.NORMAL
    assert client.responses.create.await_count == 1


@pytest.mark.asyncio
async def test_classifier_api_failure_fallback() -> None:
    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=RuntimeError("network down"))

    d = await classify_task_difficulty(
        "hello",
        ["tb1"],
        knowledge_registry=make_knowledge_registry(),
        client=client,
        agent_config=None,
    )
    assert d == TaskDifficulty.NORMAL
