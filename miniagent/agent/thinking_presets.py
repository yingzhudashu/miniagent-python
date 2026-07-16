"""业务档位与 ``ModelConfig`` thinking 字段的映射。

供 ``task_classifier``、规划步骤 ``thinking_level``、``llm_params`` 统一换算为
``(thinking_level, thinking_budget)`` 元组。

**函数选型**（二者均未知输入回落 ``medium`` / ``8192``）：

+---------------------------+------------------------------------------+
| 场景                      | 推荐函数                                 |
+===========================+==========================================+
| ``config.user.json`` 中   | :func:`map_thinking_level_to_model`      |
| profile ``thinking_level`` | 仅认英文 ``low`` / ``medium`` / ``high`` |
+---------------------------+------------------------------------------+
| 任务分类器经三档 key 换算 | :func:`map_thinking_level_to_model`      |
+---------------------------+------------------------------------------+
| 规划 / 步骤 ``thinkingLevel`` | :func:`map_business_depth`           |
| （含中文、simple/complex 等别名） | 另支持模型档位 ``light`` / ``heavy`` |
+---------------------------+------------------------------------------+

``thinking_budget`` 为传给模型 API 的推理 token 预算上限；具体消费方式见
``vendor/qwen_extra``。
"""

from __future__ import annotations

# 业务档位 → (模型 thinking_level, thinking_budget) 的唯一数据源。
#
# - low:    light  / 1024  — 快速响应（查询、格式化等）
# - medium: medium / 8192  — 标准模式（代码审查、文档分析等，默认）
# - high:   heavy  / 81920 — 深度思考（架构设计、多步推理等）
THINKING_LEVEL_PRESETS: dict[str, tuple[str, int]] = {
    "low": ("light", 1024),
    "medium": ("medium", 8192),
    "high": ("heavy", 81920),
}

# 模型原生档位名 → 业务档位 key（供 map_business_depth 透传）
_MODEL_TIER_KEYS: dict[str, str] = {
    "light": "low",
    "medium": "medium",
    "heavy": "high",
}


def map_thinking_level_to_model(level: str | None) -> tuple[str, int]:
    """将配置 / 分类器用的业务三档映射为模型 thinking 参数。

    仅识别英文 ``low`` / ``medium`` / ``high``（大小写不敏感）。
    不支持中文别名；``None``、空串及未知值均回落 ``medium``。

    Args:
        level: 业务档位字符串。

    Returns:
        ``(thinking_level, thinking_budget)`` — 模型档位（light/medium/heavy）与预算。

    Example:
        >>> map_thinking_level_to_model("high")
        ('heavy', 81920)
    """
    key = (level or "medium").strip().lower()
    return THINKING_LEVEL_PRESETS.get(key, THINKING_LEVEL_PRESETS["medium"])


def map_business_depth(level: str | None) -> tuple[str, int]:
    """将规划 / 步骤上的 ``thinkingLevel`` 映射为模型 thinking 参数。

    别名范围宽于 :func:`map_thinking_level_to_model`，并支持模型原生档位名透传。

    Args:
        level: thinkingLevel 字符串（可选），支持：

            - 简档位: ``simple``, ``low``, ``轻``, ``低``, ``light``
            - 中档位: ``normal``, ``medium``, ``中``, ``一般``（默认）
            - 高档位: ``high``, ``complex``, ``重``, ``高``, ``复杂``, ``heavy``

        ``None``、空串及未列出的值均回落 ``medium``。

    Returns:
        ``(thinking_level, thinking_budget)`` — 模型档位与预算。

    Example:
        >>> map_business_depth("复杂")
        ('heavy', 81920)
    """
    if not level:
        return THINKING_LEVEL_PRESETS["medium"]
    k = str(level).strip().lower()
    if k in _MODEL_TIER_KEYS:
        return THINKING_LEVEL_PRESETS[_MODEL_TIER_KEYS[k]]
    if k in ("simple", "low", "轻", "低"):
        return THINKING_LEVEL_PRESETS["low"]
    if k in ("normal", "medium", "中", "一般"):
        return THINKING_LEVEL_PRESETS["medium"]
    if k in ("high", "complex", "重", "高", "复杂"):
        return THINKING_LEVEL_PRESETS["high"]
    return THINKING_LEVEL_PRESETS["medium"]


__all__ = ["map_thinking_level_to_model", "map_business_depth", "THINKING_LEVEL_PRESETS"]
