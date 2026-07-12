"""Agent 难度与执行计划的 Markdown 展示。"""

from __future__ import annotations

from typing import Any

from miniagent.core.plan_utils import format_estimated_cost_block, format_output_spec_block
from miniagent.types.planning import StructuredPlan

_DIFFICULTY_LABELS = {"simple": "简单", "normal": "一般", "medium": "中等", "complex": "复杂"}


def format_task_difficulty(difficulty: Any, *, display: bool = False) -> str:
    """格式化难度；展示模式精简，历史模式包含思考深度说明。"""
    key = getattr(difficulty, "value", str(difficulty))
    label = _DIFFICULTY_LABELS.get(key, key)
    if display:
        return f"**难度** {label}（{key}）"
    return f"任务难度：{label}（{key}）\n将据此调整规划与执行的思考深度（若已启用分类器）。"


def _skip_reason(*, no_toolboxes: bool, user_skip_planning: bool, simple_classified: bool) -> str:
    """返回结构化规划被跳过的用户可读原因。"""
    if no_toolboxes:
        return "原因：无可用工具箱，未调用结构化规划器。"
    if user_skip_planning:
        return "原因：已显式跳过规划（skip_planning），未调用结构化规划器。"
    if simple_classified:
        return "原因：任务难度评估为「简单」，已跳过结构化规划。"
    return "原因：未调用结构化规划器。"


def format_plan_display_short(
    plan: StructuredPlan,
    *,
    from_llm_planner: bool,
    no_toolboxes: bool = False,
    user_skip_planning: bool = False,
    simple_classified: bool = False,
) -> str:
    """格式化适合 CLI/飞书即时展示的精简计划。"""
    if not from_llm_planner:
        reason = _skip_reason(
            no_toolboxes=no_toolboxes,
            user_skip_planning=user_skip_planning,
            simple_classified=simple_classified,
        )
        return "（已跳过结构化规划）\n" + reason + f"\n摘要：{(plan.summary or '').strip() or '—'}"
    lines = [(plan.summary or "").strip() or "—"]
    if plan.steps:
        lines.append("")
        lines.extend(
            f"{index}. {(step.description or '').strip() or '—'}"
            for index, step in enumerate(plan.steps, start=1)
        )
    if plan.required_toolboxes:
        lines.extend(("", f"工具箱：`{', '.join(plan.required_toolboxes)}`"))
    if plan.estimated_cost.total_usd > 0:
        lines.extend(("", f"预估成本约 ${plan.estimated_cost.total_usd:.4f}"))
    return "\n".join(lines)


def format_plan_message(
    plan: StructuredPlan,
    *,
    from_llm_planner: bool,
    no_toolboxes: bool = False,
    user_skip_planning: bool = False,
    simple_classified: bool = False,
) -> str:
    """格式化写入会话历史的完整计划。"""
    if not from_llm_planner:
        reason = _skip_reason(
            no_toolboxes=no_toolboxes,
            user_skip_planning=user_skip_planning,
            simple_classified=simple_classified,
        )
        return f"执行模式：跳过结构化规划。\n{reason}\n摘要：{(plan.summary or '').strip() or '—'}"
    lines = [(plan.summary or "").strip() or "—"]
    if plan.steps:
        lines.extend(("", "步骤概要："))
        for index, step in enumerate(plan.steps, start=1):
            lines.append(f"{index}. {(step.description or '').strip()}")
            if expected_input := (step.expected_input or "").strip():
                lines.append(f"预期输入：{expected_input}")
            if expected_output := (step.expected_output or "").strip():
                lines.append(f"预期产出：{expected_output}")
    if plan.required_toolboxes:
        lines.extend(("", f"涉及工具箱：{', '.join(plan.required_toolboxes)}"))
    for block in (
        format_estimated_cost_block(plan.estimated_cost),
        format_output_spec_block(plan.output_spec),
    ):
        if block:
            lines.extend(("", block))
    strategy = plan.context_strategy
    if strategy and (strategy.reason or strategy.chunks):
        lines.extend(("", "上下文策略："))
        if strategy.reason:
            lines.append(strategy.reason)
        if strategy.chunks:
            lines.append(f"分 {len(strategy.chunks)} 块执行")
    return "\n".join(lines)


__all__ = ["format_plan_display_short", "format_plan_message", "format_task_difficulty"]
