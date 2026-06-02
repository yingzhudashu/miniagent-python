"""业务档位与 ``ModelConfig`` thinking 字段的映射。

供 ``task_classifier``、规划步骤 ``thinking_level``、``llm_params`` 统一换算为
``(thinking_level, thinking_budget)`` 元组。"""

from __future__ import annotations

# 业务档位含义：
# - low: 快速响应模式，适合简单任务（如查询、格式化）。
#   thinking_level=light, budget=1024（约 1K 步推理预算）。
# - medium: 标准模式，适合中等复杂任务（如代码审查、文档分析）。
#   thinking_level=medium, budget=8192（约 8K 步推理预算）。
# - high: 深度思考模式，适合复杂任务（如架构设计、多步骤推理）。
#   thinking_level=heavy, budget=81920（约 82K 步推理预算）。
THINKING_LEVEL_PRESETS: dict[str, tuple[str, int]] = {
    "low": ("light", 1024),
    "medium": ("medium", 8192),
    "high": ("heavy", 81920),
}


def map_thinking_level_to_model(level: str) -> tuple[str, int]:
    """将业务档位字符串映射为模型 thinking 参数。

    Args:
        level: 业务档位字符串，取值范围：
            - "low": 快速响应模式，thinking_level=light, budget=1024
            - "medium": 标准模式，thinking_level=medium, budget=8192（默认）
            - "high": 深度思考模式，thinking_level=heavy, budget=81920

    Returns:
        tuple[str, int]: (thinking_level, thinking_budget) 元组
        - thinking_level: 模型推理档位（light/medium/heavy）
        - thinking_budget: 推理步数预算（整数）
    """
    key = (level or "medium").strip().lower()
    return THINKING_LEVEL_PRESETS.get(key, THINKING_LEVEL_PRESETS["medium"])


def map_business_depth(level: str | None) -> tuple[str, int]:
    """将规划/步骤上的 thinkingLevel 字符串映射为模型参数。

    支持多种命名风格，包括业务档位（simple/normal/complex）、
    技术档位（low/medium/high）以及中文别名（低/中/高）。

    Args:
        level: thinkingLevel 字符串（可选），支持取值：
            - 简档位: "simple", "low", "轻", "低"
            - 中档位: "normal", "medium", "中", "一般"（默认）
            - 高档位: "high", "complex", "重", "高", "复杂"

    Returns:
        tuple[str, int]: (thinking_level, thinking_budget) 元组
    """
    if not level:
        return THINKING_LEVEL_PRESETS["medium"]
    k = str(level).strip().lower()
    if k in ("simple", "low", "轻", "低"):
        return THINKING_LEVEL_PRESETS["low"]
    if k in ("normal", "medium", "中", "一般"):
        return THINKING_LEVEL_PRESETS["medium"]
    if k in ("high", "complex", "重", "高", "复杂"):
        return THINKING_LEVEL_PRESETS["high"]
    return THINKING_LEVEL_PRESETS["medium"]


__all__ = ["map_thinking_level_to_model", "map_business_depth", "THINKING_LEVEL_PRESETS"]