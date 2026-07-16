"""Public import and minimal-use tests for the four-module architecture."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from miniagent.agent import Agent, AgentRequest, AgentServices, AgentSettings
from miniagent.agent.types.agent import AgentRunResult
from miniagent.assistant import (
    AssistantApplication,
    create_assistant_application,
    run_assistant,
)
from miniagent.llm import LLMGateway, LLMProvider, LLMStreamEvent
from miniagent.ui import TuiApp, TuiEvent, TuiSnapshot


def test_llm_public_import_is_provider_sdk_side_effect_free() -> None:
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, miniagent.llm; assert 'openai' not in sys.modules; print('ok')",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
    assert LLMGateway and LLMProvider and LLMStreamEvent


@pytest.mark.asyncio
async def test_agent_public_facade_delegates_one_request() -> None:
    runner = AsyncMock(return_value=AgentRunResult(reply="answer"))
    agent = Agent(
        AgentServices(
            llm=object(),
            settings=AgentSettings({}),
            registry=MagicMock(),
            memory=MagicMock(),
            knowledge=MagicMock(),
            runner=runner,
        )
    )
    result = await agent.run(AgentRequest("question", session_key="s1"))
    assert result.reply == "answer"
    assert runner.await_count == 1
    assert runner.await_args.kwargs["session_key"] == "s1"


@pytest.mark.asyncio
async def test_ui_public_facade_dispatches_without_product_imports() -> None:
    actions = SimpleNamespace(
        submit=AsyncMock(),
        cancel=AsyncMock(),
        command=AsyncMock(),
        select_model=AsyncMock(),
        select_session=AsyncMock(),
        copy=AsyncMock(),
    )
    app = TuiApp(actions, TuiSnapshot(status="ready"))
    await app.dispatch(TuiEvent("submit", "hello"))
    actions.submit.assert_awaited_once_with("hello")
    assert app.snapshot.status == "ready"


def test_assistant_public_entry_points_are_exposed() -> None:
    assert AssistantApplication
    assert callable(create_assistant_application)
    assert callable(run_assistant)


@pytest.mark.parametrize(
    "legacy",
    [
        "miniagent.core",
        "miniagent.engine",
        "miniagent.contracts",
        "miniagent.infrastructure",
        "miniagent.presentation",
        "miniagent.types",
    ],
)
def test_legacy_top_level_imports_are_gone(legacy: str) -> None:
    with pytest.raises(ModuleNotFoundError):
        __import__(legacy)
