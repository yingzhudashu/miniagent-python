"""规划器的上下文格式化与无网络回退计划。"""

from __future__ import annotations

from typing import Any

from miniagent.types.config import AgentConfig
from miniagent.types.planning import (
    ContextStrategy,
    EstimatedCost,
    EstimatedTokens,
    FallbackPlan,
    OutputSpec,
    PlanStep,
    StructuredPlan,
    SuggestedConfig,
)


def format_toolbox_tool_names(registry: Any, toolbox_ids: list[str]) -> str:
    """按工具箱 ID 列出注册表中的工具名称映射。"""
    if registry is None or not toolbox_ids:
        return ""
    try:
        all_tools = registry.get_all()
    except Exception:
        return ""
    by_toolbox: dict[str, list[str]] = {}
    core: list[str] = []
    for name, tool in all_tools.items():
        if tool.toolbox is None:
            core.append(name)
        else:
            by_toolbox.setdefault(str(tool.toolbox), []).append(name)
    lines = [f"__core__（无工具箱绑定的核心工具）: {', '.join(sorted(core))}"] if core else []
    for toolbox_id in sorted(set(toolbox_ids)):
        names = sorted(by_toolbox.get(toolbox_id, []))
        lines.append(f"{toolbox_id}: {', '.join(names) if names else '(无匹配工具)'}")
    return "\n".join(lines)


def completed_work_context(agent_config: AgentConfig | None) -> str:
    """从最近会话历史提取规划器应复用的已完成工作。"""
    history = agent_config.session_config.conversation_history if agent_config else None
    if not history:
        return ""
    lines: list[str] = []
    for message in history[-20:]:
        content = str(message.get("content", "")) if isinstance(message, dict) else ""
        lowered = content.lower()
        if content and any(
            term in lowered
            for term in ("read_file", "已读取", "分析", "测试", "pytest", "已完成", "rag", "知识库")
        ):
            lines.append(f"- {content[:180]}")
    if not lines:
        return ""
    return "## 最近已完成工作（规划时应复用，避免重复步骤）\n" + "\n".join(lines[-8:])


def fallback_plan(user_input: str) -> StructuredPlan:
    """在规划器最终失败时生成低风险单步计划。"""
    return StructuredPlan(
        summary="直接执行模式：跳过详细规划",
        steps=[
            PlanStep(
                step_number=1,
                description="根据用户需求直接处理",
                required_toolboxes=[],
                expected_input=user_input,
                expected_output="用户需求的回复",
                thinking_level="low",
            )
        ],
        required_toolboxes=[],
        suggested_config=SuggestedConfig(max_turns=5, tool_timeout=30, risk_level="low"),
        estimated_tokens=EstimatedTokens(
            prompt_tokens=500,
            completion_tokens=500,
            tool_result_tokens=200,
            total=1200,
        ),
        context_strategy=ContextStrategy(mode="normal", reason="简单任务"),
        requires_confirmation=False,
        risk_level="low",
        estimated_cost=EstimatedCost(input_tokens=500, output_tokens=500, total_usd=0.0),
        output_spec=OutputSpec(
            language="zh-CN",
            format="markdown",
            expected_deliverable="直接回复",
        ),
        fallback_plan=FallbackPlan(degrade_to_simple=False, degraded_max_turns=5),
    )


__all__ = ["completed_work_context", "fallback_plan", "format_toolbox_tool_names"]
