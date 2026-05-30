"""业务档位与 ``ModelConfig`` thinking 字段的映射（OpenClaw ``thinkingDefault`` 语义对齐）。

供 ``task_classifier``、规划步骤 ``thinking_level``、``llm_params`` 统一换算为
``(thinking_level, thinking_budget)`` 元组。"""

from __future__ import annotations

# OpenClaw agents.defaults.thinkingDefault 语义对齐。
# 业务档位含义：
# - low: 快速响应模式，适合简单任务（如查询、格式化）。
#   thinking_level=light, budget=1024（约 1K 步推理预算）。
# - medium: 标准模式，适合中等复杂任务（如代码审查、文档分析）。
#   thinking_level=medium, budget=8192（约 8K 步推理预算）。
# - high: 深度思考模式，适合复杂任务（如架构设计、多步骤推理）。
#   thinking_level=heavy, budget=81920（约 82K 步推理预算）。
OPENCLAW_TO_MODEL: dict[str, tuple[str, int]] = {
    "low": ("light", 1024),
    "medium": ("medium", 8192),
    "high": ("heavy", 81920),
}


def map_openclaw_thinking_to_model(level: str) -> tuple[str, int]:
    """low/medium/high -> (thinking_level, thinking_budget)。"""
    key = (level or "medium").strip().lower()
    return OPENCLAW_TO_MODEL.get(key, OPENCLAW_TO_MODEL["medium"])


def map_business_depth(level: str | None) -> tuple[str, int]:
    """规划/步骤上的 thinkingLevel 字符串 -> (thinking_level, budget)。"""
    if not level:
        return OPENCLAW_TO_MODEL["medium"]
    k = str(level).strip().lower()
    if k in ("simple", "low", "轻", "低"):
        return OPENCLAW_TO_MODEL["low"]
    if k in ("normal", "medium", "中", "一般"):
        return OPENCLAW_TO_MODEL["medium"]
    if k in ("high", "complex", "重", "高", "复杂"):
        return OPENCLAW_TO_MODEL["high"]
    return OPENCLAW_TO_MODEL["medium"]


__all__ = ["map_openclaw_thinking_to_model", "map_business_depth", "OPENCLAW_TO_MODEL"]
