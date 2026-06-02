"""Mini Agent Python — 模型管理命令

显示和切换当前使用的LLM模型。
"""

from __future__ import annotations

import os

from miniagent.infrastructure.json_config import get_config


def get_current_model() -> str:
    """获取当前模型名称。

    Returns:
        当前模型名称（从JSON配置读取，默认gpt-4o-mini）
    """
    return get_config("model.model", "gpt-4o-mini")


def switch_model(new_model: str) -> str:
    """切换模型（运行时）。

    注意：仅修改环境变量，不影响已创建的OpenAI客户端实例。
    新的API调用将使用新模型。

    Args:
        new_model: 新模型名称

    Returns:
        操作结果消息
    """
    old_model = get_current_model()
    os.environ["MINIAGENT_MODEL_MODEL"] = new_model
    return f"✅ 模型已切换: {old_model} → {new_model}"


def format_model_info() -> str:
    """格式化模型信息显示。

    Returns:
        格式化的模型信息文本
    """
    current_model = get_current_model()

    lines = [
        "## 当前模型配置",
        "",
        f"**模型**: `{current_model}`",
        "",
        "### 可用模型示例",
        "- `gpt-4o-mini`: 快速响应，成本低（推荐）",
        "- `gpt-4o`: 高质量，中等成本",
        "- `gpt-4-turbo`: 最高质量，高成本",
        "- `gpt-3.5-turbo`: 传统快速模型",
        "",
        "### 使用方式",
        "```",
        "/model               # 显示当前模型",
        "/model gpt-4o        # 切换到gpt-4o模型",
        "```",
        "",
        "**注意**: 模型切换仅影响新的API调用，不影响当前会话中已创建的客户端。",
    ]

    return "\n".join(lines)


__all__ = ["get_current_model", "switch_model", "format_model_info"]