"""任务难度分类：映射函数与 JSON 解析（无网络）。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from miniagent.core._openai_compat import json_object_unsupported
from miniagent.core.task_classifier import (
    TaskDifficulty,
    classify_task_difficulty,
    default_step_thinking_for_difficulty,
    exec_merge_for_simple_path,
    planner_merge_for_difficulty,
)


def test_planner_merge_scales_with_difficulty() -> None:
    low = planner_merge_for_difficulty(TaskDifficulty.NORMAL)
    mid = planner_merge_for_difficulty(TaskDifficulty.MEDIUM)
    high = planner_merge_for_difficulty(TaskDifficulty.COMPLEX)
    assert low["thinking_budget"] < high["thinking_budget"]
    assert mid["thinking_budget"] <= high["thinking_budget"]


def test_default_step_thinking_mapping() -> None:
    assert default_step_thinking_for_difficulty(TaskDifficulty.NORMAL) == "low"
    assert default_step_thinking_for_difficulty(TaskDifficulty.MEDIUM) == "medium"
    assert default_step_thinking_for_difficulty(TaskDifficulty.COMPLEX) == "high"


def test_exec_merge_simple_path() -> None:
    m = exec_merge_for_simple_path()
    assert "thinking_level" in m and "thinking_budget" in m


def test_json_object_unsupported_ignores_missing_json_keyword() -> None:
    err = Exception(
        "Response input messages must contain the word 'json' in some form "
        "to use 'response.format' of type 'json_object'."
    )
    assert not json_object_unsupported(err)


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

    d = await classify_task_difficulty("hello", ["tb1"], client=client, agent_config=None)
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

    d = await classify_task_difficulty("hello", ["tb1"], client=client, agent_config=None)

    assert d == TaskDifficulty.NORMAL
    assert captured["response_format"] == {"type": "json_object"}
    messages = captured["messages"]
    assert isinstance(messages, list)
    user_messages = [m for m in messages if m.get("role") == "user"]
    assert any("json" in str(m.get("content", "")).lower() for m in user_messages)
