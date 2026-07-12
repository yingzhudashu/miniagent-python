"""跳过结构化规划时的确定性默认计划。"""

from __future__ import annotations

from miniagent.types.planning import (
    ContextStrategy,
    EstimatedTokens,
    StructuredPlan,
    SuggestedConfig,
)

_NO_TOOL_PATTERNS = (
    "不调用工具", "不要调用工具", "无需调用工具", "禁止调用工具",
    "do not use tools", "don't use tools", "without tools", "no tools",
)


def user_forbids_tools(user_input: str) -> bool:
    """判断用户是否明确要求仅文本回复。"""
    normalized = " ".join((user_input or "").lower().split())
    return any(pattern in normalized for pattern in _NO_TOOL_PATTERNS)


def create_default_plan(*, tools_enabled: bool = True) -> StructuredPlan:
    """创建不分步、低风险且沿用全局轮数的直接执行计划。"""
    return StructuredPlan(
        summary="直接执行模式",
        steps=[],
        required_toolboxes=[],
        suggested_config=SuggestedConfig(max_turns=None, tool_timeout=30, risk_level="low"),
        estimated_tokens=EstimatedTokens(),
        context_strategy=ContextStrategy(mode="normal", reason="跳过规划"),
        requires_confirmation=False,
        risk_level="low",
        tools_enabled=tools_enabled,
    )


__all__ = ["create_default_plan", "user_forbids_tools"]
