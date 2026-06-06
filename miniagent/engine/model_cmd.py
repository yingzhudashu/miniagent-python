"""Mini Agent Python — 模型管理命令

显示和切换当前使用的 LLM 模型（写入 config.user.json）。
"""

from __future__ import annotations

import json
from pathlib import Path

from miniagent.infrastructure.json_config import get_config, reload_config


def get_current_model() -> str:
    return get_config("model.model", "gpt-4o-mini")


def switch_model(new_model: str) -> str:
    """切换模型：更新 config.user.json 中的 model.model。"""
    old_model = get_current_model()
    project_root = Path(__file__).parent.parent.parent
    user_path = project_root / "config.user.json"

    existing: dict = {}
    if user_path.exists():
        try:
            existing = json.loads(user_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}

    model_section = dict(existing.get("model", {}))
    model_section["model"] = new_model
    existing["model"] = model_section

    user_path.write_text(
        json.dumps(existing, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    reload_config()
    return f"✅ 模型已切换: {old_model} → {new_model}（已写入 config.user.json）"


def format_model_info() -> str:
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
        "/model gpt-4o        # 切换到 gpt-4o 模型",
        "```",
        "",
        "**注意**: 持久化修改请编辑 `config.user.json` 的 `model.model` 字段。",
    ]

    return "\n".join(lines)


__all__ = ["get_current_model", "switch_model", "format_model_info"]
