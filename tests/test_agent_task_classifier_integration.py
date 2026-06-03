"""run_agent + 任务分类：简单路径不调用 generate_plan。"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from tests.llm_helpers import mock_all_llm_clients
from miniagent.core.agent import run_agent
from miniagent.infrastructure.registry import DefaultToolRegistry
from miniagent.types.tool import Toolbox


@pytest.mark.asyncio
async def test_simple_difficulty_skips_planner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINIAGENT_TASK_CLASSIFIER", "1")
    monkeypatch.setenv("MINIAGENT_FEATURES_REFLECTION", "0")
    tb = Toolbox(id="fs", name="fs", description="files", keywords=[])

    with mock_all_llm_clients():
        with patch(
            "miniagent.core.agent.classify_task_difficulty",
            new_callable=AsyncMock,
        ) as clf:
            from miniagent.core.task_classifier import TaskDifficulty

            clf.return_value = TaskDifficulty.SIMPLE
            with patch("miniagent.core.agent.generate_plan", new_callable=AsyncMock) as gp:
                with patch("miniagent.core.agent.execute_plan", new_callable=AsyncMock) as ex:
                    ex.return_value = "ok"
                    out = await run_agent(
                        "hello",
                        registry=DefaultToolRegistry(),
                        toolboxes=[tb],
                    )
    assert out == "ok"
    gp.assert_not_called()
    ex.assert_called_once()


@pytest.mark.asyncio
async def test_classifier_off_always_plans(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINIAGENT_TASK_CLASSIFIER", "0")
    monkeypatch.setenv("MINIAGENT_FEATURES_REFLECTION", "0")
    tb = Toolbox(id="fs", name="fs", description="files", keywords=[])

    from miniagent.types.planning import StructuredPlan

    fake_plan = StructuredPlan(summary="x", steps=[], required_toolboxes=[])

    with mock_all_llm_clients():
        with patch("miniagent.core.agent.generate_plan", new_callable=AsyncMock) as gp:
            gp.return_value = fake_plan
            with patch("miniagent.core.agent.execute_plan", new_callable=AsyncMock) as ex:
                ex.return_value = "done"
                await run_agent("task", registry=DefaultToolRegistry(), toolboxes=[tb])
    gp.assert_called_once()
    ex.assert_called_once()


@pytest.mark.asyncio
async def test_suggested_thinking_level_merges_into_model_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MINIAGENT_TASK_CLASSIFIER", "0")
    monkeypatch.setenv("MINIAGENT_FEATURES_REFLECTION", "0")
    tb = Toolbox(id="fs", name="fs", description="files", keywords=[])

    from miniagent.types.planning import StructuredPlan, SuggestedConfig

    fake_plan = StructuredPlan(
        summary="x",
        steps=[],
        required_toolboxes=[],
        suggested_config=SuggestedConfig(thinking_level="low"),
    )

    captured: dict[str, object] = {}

    async def _capture_exec(
        _plan: object,
        _user_input: str,
        _registry: object,
        _monitor: object,
        merged_config: object,
        *_a: object,
        **_k: object,
    ) -> str:
        captured["thinking_level"] = getattr(merged_config, "model_overrides", {}).get(
            "thinking_level"
        )
        captured["thinking_budget"] = getattr(merged_config, "model_overrides", {}).get(
            "thinking_budget"
        )
        return "ok"

    with mock_all_llm_clients():
        with patch("miniagent.core.agent.generate_plan", new_callable=AsyncMock) as gp:
            gp.return_value = fake_plan
            with patch("miniagent.core.agent.execute_plan", new_callable=AsyncMock) as ex:
                ex.side_effect = _capture_exec
                await run_agent("task", registry=DefaultToolRegistry(), toolboxes=[tb])

    assert captured.get("thinking_level") == "light"
    assert captured.get("thinking_budget") == 1024
