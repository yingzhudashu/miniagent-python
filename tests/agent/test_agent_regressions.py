"""Focused regressions migrated from test_diff_gate_new_modules.py."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from miniagent.agent import agent_display, planner_support
from miniagent.agent.types.config import AgentConfig, SessionBindingConfig
from miniagent.agent.types.planning import (
    ContextStrategy,
    EstimatedCost,
    OutputSpec,
    PlanChunk,
    PlanStep,
    StructuredPlan,
)


def test_agent_display_and_planner_support_branches() -> None:
    step = PlanStep(
        step_number=1,
        description="do",
        required_toolboxes=["fs"],
        expected_input="input",
        expected_output="output",
    )
    plan = StructuredPlan(
        summary="summary",
        steps=[step],
        required_toolboxes=["fs"],
        estimated_cost=EstimatedCost(total_usd=0.5),
        output_spec=OutputSpec(language="zh-CN", format="markdown", expected_deliverable="report"),
        context_strategy=ContextStrategy(
            mode="chunked", reason="large", chunks=[PlanChunk(chunk_number=1, steps=[step])]
        ),
    )
    assert "简单" in agent_display.format_task_difficulty("simple", display=True)
    assert "思考深度" in agent_display.format_task_difficulty("unknown")
    short = agent_display.format_plan_display_short(plan, from_llm_planner=True)
    full = agent_display.format_plan_message(plan, from_llm_planner=True)
    assert "预估成本" in short and "工具箱" in short
    assert all(part in full for part in ("预期输入", "预期产出", "上下文策略", "分 1 块"))
    for flags, fragment in (
        ({"no_toolboxes": True}, "无可用工具箱"),
        ({"user_skip_planning": True}, "显式跳过"),
        ({"simple_classified": True}, "简单"),
        ({}, "未调用"),
    ):
        rendered = agent_display.format_plan_display_short(
            plan, from_llm_planner=False, **flags
        )
        assert fragment in rendered

    config = AgentConfig(
        session_config=SessionBindingConfig(
            conversation_history=[
                {"role": "assistant", "content": "已完成 pytest"},
                {"role": "assistant", "content": "unrelated"},
            ]
        )
    )
    assert "已完成 pytest" in planner_support.completed_work_context(config)
    assert planner_support.completed_work_context(None) == ""
    registry = SimpleNamespace(get_all=lambda: {
        "read": SimpleNamespace(toolbox=None),
        "search": SimpleNamespace(toolbox="web"),
    })
    mapping = planner_support.format_toolbox_tool_names(registry, ["web", "empty"])
    assert "__core__" in mapping and "search" in mapping and "无匹配工具" in mapping
    assert planner_support.format_toolbox_tool_names(
        SimpleNamespace(get_all=lambda: (_ for _ in ()).throw(RuntimeError())), ["web"]
    ) == ""
    fallback = planner_support.fallback_plan("request")
    assert fallback.steps[0].expected_input == "request" and not fallback.fallback_plan.degrade_to_simple

@pytest.mark.asyncio
async def test_agent_reflection_cache_and_disabled_path(monkeypatch: pytest.MonkeyPatch) -> None:
    from miniagent.agent import agent

    monkeypatch.setattr(agent, "get_config", lambda *_args, **_kwargs: False)
    assert await agent._reflect_agent_reply(
        "q", "reply", knowledge_registry=object(), client=object(),
        session_key="s", engine=object(),
    ) == "reply"

    monkeypatch.setattr(agent, "get_config", lambda *_args, **_kwargs: True)
    reflection = SimpleNamespace(score=1)
    monkeypatch.setattr(agent, "reflect_on_result", AsyncMock(return_value=reflection))
    monkeypatch.setattr(agent, "build_reflection_footer", lambda _reflection: " footer")
    engine = SimpleNamespace(_last_reflection=None)
    result = await agent._reflect_agent_reply(
        "q", "reply", knowledge_registry=object(), client=object(),
        session_key=None, engine=engine,
    )
    assert result == "reply footer" and engine._last_reflection["default"] is reflection

@pytest.mark.asyncio
async def test_agent_clarification_answer_and_failure_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from miniagent.agent import agent
    from miniagent.agent.task_classifier import TaskDifficulty

    thinking = AsyncMock()
    channel = SimpleNamespace(
        request_confirmation=AsyncMock(
            return_value=SimpleNamespace(rejected=False, adjustment="answer")
        )
    )

    async def clarify(_input, *, ask_user, **_kwargs):
        assert await ask_user("question") == "answer"
        return SimpleNamespace(clarified_goal="clear")

    clarifier = SimpleNamespace(
        clarify=clarify,
        to_system_prompt=lambda _result: "clarified prompt",
    )
    monkeypatch.setattr(agent, "_announce_difficulty_and_plan_enabled", lambda: True)
    result = await agent._clarify_user_input(
        "input", difficulty=TaskDifficulty.NORMAL, clarifier=clarifier,
        confirmation_channel=channel, on_thinking=thinking,
        knowledge_registry=object(), memory=SimpleNamespace(store=object()),
        client=object(), session_key="s",
    )
    assert result.endswith("clarified prompt") and thinking.await_count >= 3

    failing = SimpleNamespace(clarify=AsyncMock(side_effect=RuntimeError("bad")))
    assert await agent._clarify_user_input(
        "original", difficulty=TaskDifficulty.NORMAL, clarifier=failing,
        confirmation_channel=None, on_thinking=None, knowledge_registry=object(),
        memory=SimpleNamespace(store=object()), client=object(), session_key="s",
    ) == "original"

@pytest.mark.asyncio
async def test_agent_high_risk_plan_cancel_with_thinking_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from miniagent.agent import agent
    from miniagent.agent.task_classifier import TaskDifficulty

    plan = StructuredPlan(summary="risk", requires_confirmation=True)
    monkeypatch.setattr(agent, "generate_plan", AsyncMock(return_value=plan))
    monkeypatch.setattr(agent, "invoke_on_thinking", AsyncMock(side_effect=RuntimeError("sink")))
    on_plan = AsyncMock(
        return_value=SimpleNamespace(plan_action=lambda: ("cancel", None))
    )
    prepared, _config, from_llm, reply = await agent._prepare_plan(
        "input", toolboxes=[SimpleNamespace(id="tb")], skip_planning=False,
        difficulty=TaskDifficulty.NORMAL, config=AgentConfig(), registry=object(),
        knowledge_registry=object(), client=object(), on_plan=on_plan,
        on_thinking=AsyncMock(), session_key="s",
    )
    assert prepared is None and from_llm and "已取消" in (reply or "")
