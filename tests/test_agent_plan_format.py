"""规划文案写入 on_thinking：完整步骤列表。"""

from __future__ import annotations

from miniagent.core.agent import _format_plan_message
from miniagent.types.planning import PlanStep, StructuredPlan


def test_format_plan_lists_all_steps_without_ellipsis() -> None:
    steps = [
        PlanStep(step_number=i, description=f"描述{i}-" + "x" * 400, expected_input="in", expected_output="out")
        for i in range(1, 31)
    ]
    plan = StructuredPlan(summary="摘要", steps=steps, required_toolboxes=["tb1", "tb2"])
    text = _format_plan_message(plan, from_llm_planner=True)
    assert "描述30-" in text
    assert "此处仅列前" not in text
    assert "预期输入：in" in text
    assert "涉及工具箱：tb1, tb2" in text
