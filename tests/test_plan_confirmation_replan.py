"""计划确认：/adjust 触发重规划。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from miniagent.agent.agent import run_agent
from miniagent.agent.tools.registry import DefaultToolRegistry
from miniagent.agent.types.confirmation import ConfirmationResult
from miniagent.agent.types.planning import StructuredPlan
from miniagent.agent.types.tool import Toolbox
from tests.config_helpers import install_test_config
from tests.memory_helpers import make_knowledge_registry, make_memory_runtime


@pytest.mark.asyncio
async def test_on_plan_adjust_triggers_replan(tmp_path) -> None:
    install_test_config(tmp_path, {"features": {"reflection": False}})
    tb = Toolbox(id="fs", name="fs", description="files", keywords=[])

    first = StructuredPlan(
        summary="delete all",
        steps=[],
        required_toolboxes=[],
        requires_confirmation=True,
    )
    second = StructuredPlan(
        summary="read only",
        steps=[],
        required_toolboxes=[],
        requires_confirmation=False,
    )

    plan_calls: list[str] = []

    async def fake_generate(user_input, *args, **kwargs):
        plan_calls.append(user_input)
        return first if len(plan_calls) == 1 else second

    async def fake_on_plan(_plan: object) -> ConfirmationResult:
        return ConfirmationResult.adjust("不要删除，只读即可")

    with (
        patch("miniagent.agent.constants.EXECUTION_TASK_CLASSIFIER_ENABLED", False),
        patch("miniagent.agent.constants.EXECUTION_ANNOUNCE_DIFFICULTY", False),
        patch("miniagent.agent.agent.generate_plan", side_effect=fake_generate),
        patch("miniagent.agent.agent.execute_plan", new_callable=AsyncMock) as ex,
    ):
        ex.return_value = "done"

        out = await run_agent(
            "分析目录",
            registry=DefaultToolRegistry(),
            memory=make_memory_runtime(),
            knowledge_registry=make_knowledge_registry(),
            client=MagicMock(),
            toolboxes=[tb],
            on_plan=fake_on_plan,
        )

    assert len(plan_calls) == 2
    assert "[用户计划调整]" in plan_calls[1]
    assert "不要删除" in plan_calls[1]
    assert out.reply == "done"
    ex.assert_awaited_once()
