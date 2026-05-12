"""Tests for shared AsyncOpenAI factory and injectable planner client."""

from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from miniagent.core.openai_client import (
    get_shared_async_openai,
    reset_shared_async_openai_for_tests,
)


def test_get_shared_async_openai_singleton() -> None:
    reset_shared_async_openai_for_tests()
    os.environ["OPENAI_API_KEY"] = "sk-test-placeholder-for-singleton"
    try:
        a = get_shared_async_openai()
        b = get_shared_async_openai()
        assert a is b
    finally:
        os.environ.pop("OPENAI_API_KEY", None)
        reset_shared_async_openai_for_tests()


@pytest.mark.asyncio
async def test_generate_plan_accepts_injected_client() -> None:
    from miniagent.core.planner import generate_plan
    from miniagent.types.tool import Toolbox

    reset_shared_async_openai_for_tests()
    valid_json = json.dumps(
        {
            "summary": "t",
            "steps": [],
            "requiredToolboxes": [],
            "suggestedConfig": {},
            "estimatedTokens": {},
            "contextStrategy": {},
            "requiresConfirmation": False,
            "riskLevel": "low",
            "estimatedCost": {},
            "outputSpec": {},
            "fallbackPlan": {},
        }
    )
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content=valid_json))]
    mock_response.usage = None

    client = MagicMock()
    client.chat = MagicMock()
    client.chat.completions = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=mock_response)

    plan = await generate_plan(
        "hello",
        [Toolbox(id="t", name="T", description="d")],
        client=client,
    )
    assert plan.summary == "t"
    client.chat.completions.create.assert_awaited()
