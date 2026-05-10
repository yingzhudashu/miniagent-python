"""任务分类器：json_object 不支持时重试。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from miniagent.core.task_classifier import TaskDifficulty, classify_task_difficulty


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
