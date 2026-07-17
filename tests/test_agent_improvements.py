"""Regression tests for agent.py doc/edge-path improvements."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from miniagent.agent.agent import (
    _format_plan_display_short,
    _format_plan_message,
    _merge_plan_suggested_config,
    run_agent,
)
from miniagent.agent.tools.registry import DefaultToolRegistry
from miniagent.agent.types.config import AgentConfig
from miniagent.agent.types.confirmation import ConfirmationResult
from miniagent.agent.types.error_prefix import WARNING_PREFIX
from miniagent.agent.types.planning import (
    ContextStrategy,
    FallbackPlan,
    PlanStep,
    StructuredPlan,
    SuggestedConfig,
)
from miniagent.agent.types.tool import Toolbox
from tests.config_helpers import install_test_config
from tests.memory_helpers import make_knowledge_registry, make_memory_runtime


def test_format_plan_display_short_omits_expected_io() -> None:
    """精简展示不含 expected_input/output，与全量格式对偶。"""
    plan = StructuredPlan(
        summary="Do things",
        steps=[
            PlanStep(
                step_number=1,
                description="第一步",
                expected_input="in",
                expected_output="out",
            ),
        ],
        required_toolboxes=["fs"],
    )
    full = _format_plan_message(plan, from_llm_planner=True)
    short = _format_plan_display_short(plan, from_llm_planner=True)

    assert "第一步" in full and "第一步" in short
    assert "预期输入：in" in full
    assert "预期输入" not in short
    assert "预期产出" not in short
    assert "Do things" in short
    assert "工具箱：`fs`" in short


def test_format_plan_display_short_skipped_reason_matches_full() -> None:
    """跳过规划时，精简与全量格式应给出同一类原因。"""
    plan = StructuredPlan(summary="直接执行模式", steps=[], required_toolboxes=[])
    full = _format_plan_message(plan, from_llm_planner=False, user_skip_planning=True)
    short = _format_plan_display_short(plan, from_llm_planner=False, user_skip_planning=True)

    assert "显式跳过规划" in full
    assert "显式跳过规划" in short
    assert "直接执行模式" in full
    assert "直接执行模式" in short


def test_merge_plan_suggested_config_max_turns_does_not_shrink() -> None:
    base = AgentConfig(max_turns=100)
    plan = StructuredPlan(
        summary="s",
        steps=[],
        required_toolboxes=[],
        suggested_config=SuggestedConfig(max_turns=5),
    )
    out = _merge_plan_suggested_config(plan, base)
    assert out.max_turns == 100


def test_merge_plan_suggested_config_raises_max_turns() -> None:
    base = AgentConfig(max_turns=100)
    plan = StructuredPlan(
        summary="s",
        steps=[],
        required_toolboxes=[],
        suggested_config=SuggestedConfig(max_turns=300),
    )
    out = _merge_plan_suggested_config(plan, base)
    assert out.max_turns == 300


def test_merge_plan_suggested_config_sequential_disables_parallel() -> None:
    base = AgentConfig(max_turns=10, allow_parallel_tools=True)
    plan = StructuredPlan(
        summary="s",
        steps=[],
        required_toolboxes=[],
        suggested_config=SuggestedConfig(parallelism="sequential"),
    )
    out = _merge_plan_suggested_config(plan, base)
    assert out.allow_parallel_tools is False


def test_merge_plan_suggested_config_overflow_from_context_strategy() -> None:
    base = AgentConfig(max_turns=10, context_overflow_strategy="error")
    plan = StructuredPlan(
        summary="s",
        steps=[],
        required_toolboxes=[],
        suggested_config=SuggestedConfig(),
        context_strategy=ContextStrategy(mode="summarize", reason="big task"),
    )
    out = _merge_plan_suggested_config(plan, base)
    assert out.context_overflow_strategy == "summarize"


def test_merge_plan_risk_level_from_plan_when_config_none() -> None:
    base = AgentConfig(max_turns=10, risk_level=None)
    plan = StructuredPlan(
        summary="s",
        steps=[],
        required_toolboxes=[],
        risk_level="high",
    )
    out = _merge_plan_suggested_config(plan, base)
    assert out.risk_level == "high"


@pytest.mark.asyncio
async def test_fallback_degrade_retries_with_empty_steps(tmp_path) -> None:
    install_test_config(tmp_path, {"features": {"reflection": False}})
    tb = Toolbox(id="fs", name="fs", description="files", keywords=[])

    plan = StructuredPlan(
        summary="multi step",
        steps=[PlanStep(step_number=1, description="step1")],
        required_toolboxes=["fs"],
        fallback_plan=FallbackPlan(degrade_to_simple=True, degraded_max_turns=5),
    )
    step_counts: list[int] = []

    async def fake_execute(plan_arg: StructuredPlan, *_a: object, **_k: object) -> str:
        step_counts.append(len(plan_arg.steps))
        if len(step_counts) == 1:
            return f"{WARNING_PREFIX} phased failed"
        return "recovered"

    with (
        patch("miniagent.agent.constants.EXECUTION_TASK_CLASSIFIER_ENABLED", False),
        patch("miniagent.agent.constants.EXECUTION_ANNOUNCE_DIFFICULTY", False),
        patch("miniagent.agent.agent.generate_plan", new_callable=AsyncMock) as gp,
        patch("miniagent.agent.agent.execute_plan", side_effect=fake_execute),
    ):
        gp.return_value = plan
        out = await run_agent(
            "task",
            registry=DefaultToolRegistry(),
            memory=make_memory_runtime(),
            knowledge_registry=make_knowledge_registry(),
            client=MagicMock(),
            toolboxes=[tb],
        )

    assert step_counts == [1, 0]
    assert out.reply == "recovered"


@pytest.mark.asyncio
async def test_replan_exhaustion_cancels(tmp_path) -> None:
    install_test_config(tmp_path, {"features": {"reflection": False}})
    tb = Toolbox(id="fs", name="fs", description="files", keywords=[])

    risky = StructuredPlan(
        summary="risk",
        steps=[],
        required_toolboxes=[],
        requires_confirmation=True,
    )

    async def fake_on_plan(_plan: object) -> ConfirmationResult:
        return ConfirmationResult.adjust("请改成只读")

    with (
        patch("miniagent.agent.constants.EXECUTION_TASK_CLASSIFIER_ENABLED", False),
        patch("miniagent.agent.constants.EXECUTION_ANNOUNCE_DIFFICULTY", False),
        patch("miniagent.agent.agent.EXECUTION_MAX_PLAN_CONFIRM_ROUNDS", 1),
        patch("miniagent.agent.agent.generate_plan", new_callable=AsyncMock) as gp,
        patch("miniagent.agent.agent.execute_plan", new_callable=AsyncMock) as ex,
    ):
        gp.return_value = risky
        out = await run_agent(
            "task",
            registry=DefaultToolRegistry(),
            memory=make_memory_runtime(),
            knowledge_registry=make_knowledge_registry(),
            client=MagicMock(),
            toolboxes=[tb],
            on_plan=fake_on_plan,
        )

    assert "计划调整次数过多" in out.reply
    ex.assert_not_called()


@pytest.mark.asyncio
async def test_requires_confirmation_without_on_plan_proceeds(tmp_path) -> None:
    install_test_config(tmp_path, {"features": {"reflection": False}})
    tb = Toolbox(id="fs", name="fs", description="files", keywords=[])

    risky = StructuredPlan(
        summary="risk",
        steps=[],
        required_toolboxes=[],
        requires_confirmation=True,
    )

    with (
        patch("miniagent.agent.constants.EXECUTION_TASK_CLASSIFIER_ENABLED", False),
        patch("miniagent.agent.constants.EXECUTION_ANNOUNCE_DIFFICULTY", False),
        patch("miniagent.agent.agent.generate_plan", new_callable=AsyncMock) as gp,
        patch("miniagent.agent.agent.execute_plan", new_callable=AsyncMock) as ex,
    ):
        gp.return_value = risky
        ex.return_value = "executed without confirm channel"
        out = await run_agent(
            "task",
            registry=DefaultToolRegistry(),
            memory=make_memory_runtime(),
            knowledge_registry=make_knowledge_registry(),
            client=MagicMock(),
            toolboxes=[tb],
        )

    assert out.reply == "executed without confirm channel"
    ex.assert_awaited_once()


@pytest.mark.asyncio
async def test_reflection_stored_on_engine(tmp_path) -> None:
    from miniagent.agent.problem_solver import ReflectionResult

    install_test_config(tmp_path, {"features": {"reflection": True}})
    engine = MagicMock()
    engine._last_reflection = {}

    with (
        patch("miniagent.agent.constants.EXECUTION_TASK_CLASSIFIER_ENABLED", False),
        patch("miniagent.agent.agent.execute_plan", new_callable=AsyncMock) as ex,
        patch("miniagent.agent.agent.reflect_on_result", new_callable=AsyncMock) as ref,
    ):
        ex.return_value = "done"
        ref.return_value = ReflectionResult(
            acceptable=True,
            quality_score=0.9,
            issues=[],
            suggestions=[],
        )
        registry = MagicMock()
        registry.get_schemas.return_value = []
        registry.get_all.return_value = {}
        registry.list.return_value = []

        await run_agent(
            "task",
            registry=registry,
            memory=make_memory_runtime(),
            knowledge_registry=make_knowledge_registry(),
            client=MagicMock(),
            skip_planning=True,
            session_key="sess-1",
            engine=engine,
        )

    assert "sess-1" in engine._last_reflection
    assert engine._last_reflection["sess-1"].quality_score == 0.9
