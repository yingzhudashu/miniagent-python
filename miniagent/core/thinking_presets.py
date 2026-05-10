"""业务档位与 ``ModelConfig`` thinking 字段的映射（OpenClaw ``thinkingDefault`` 语义对齐）。

供 ``task_classifier``、规划步骤 ``thinking_level``、``llm_params`` 统一换算为
``(thinking_level, thinking_budget)`` 元组。"""

from __future__ import annotations

# OpenClaw agents.defaults.thinkingDefault 语义对齐
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
