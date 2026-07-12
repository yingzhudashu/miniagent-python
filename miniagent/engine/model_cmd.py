"""Mini Agent Python — 模型管理命令

显示和切换当前使用的 LLM 模型（写入 config.user.json）。
切换后会调用 ``reload_config()``，与配置热更新监听（``config_watch``）行为一致。
"""

from __future__ import annotations

import json

from miniagent.infrastructure.atomic_json import atomic_dump_json
from miniagent.infrastructure.json_config import (
    get_config,
    get_user_config_path,
    reload_config,
)
from miniagent.types.error_prefix import ERROR_PREFIX, SUCCESS_PREFIX


def get_current_model() -> str:
    """读取当前生效的 ``model.model`` 配置值。

    合并顺序为 ``config.user.json`` 覆盖包内 defaults。
    空字符串或非字符串异常值回退到 ``gpt-4o-mini``。
    """
    value = get_config("model.model", "gpt-4o-mini")
    if isinstance(value, str):
        trimmed = value.strip()
        return trimmed if trimmed else "gpt-4o-mini"
    if value is not None:
        return str(value)
    return "gpt-4o-mini"


def switch_model(new_model: str) -> str:
    """切换模型：合并写入 ``config.user.json`` 的 ``model.model`` 字段。

    保留 user 文件中其他顶层节及 ``model`` 节内已有字段；写入成功后
    调用 ``reload_config()`` 使内存配置立即生效。

    Args:
        new_model: 目标模型 id（前后空白会被剔除）。

    Returns:
        成功或失败的人类可读消息（不抛出预期内的校验/IO 错误）。
    """
    candidate = new_model.strip()
    if not candidate:
        return f"{ERROR_PREFIX} 模型名不能为空"

    old_model = get_current_model()
    user_path = get_user_config_path()

    if user_path.exists():
        try:
            raw = user_path.read_text(encoding="utf-8")
            existing = json.loads(raw)
        except json.JSONDecodeError as exc:
            return (
                f"{ERROR_PREFIX} config.user.json 格式无效，无法切换模型。"
                f"请先修复 JSON 后再试（{exc}）"
            )
        except OSError as exc:
            return f"{ERROR_PREFIX} 无法读取 config.user.json: {exc}"
    else:
        existing = {}

    if not isinstance(existing, dict):
        return f"{ERROR_PREFIX} config.user.json 根节点必须是 JSON 对象"

    model_raw = existing.get("model", {})
    if model_raw is None:
        model_raw = {}
    if not isinstance(model_raw, dict):
        return (
            f"{ERROR_PREFIX} config.user.json 的 `model` 节必须是对象，"
            f"当前类型为 {type(model_raw).__name__}"
        )

    model_section = dict(model_raw)
    model_section["model"] = candidate
    existing["model"] = model_section

    try:
        atomic_dump_json(user_path, existing, indent=2, ensure_ascii=False)
    except OSError as exc:
        return f"{ERROR_PREFIX} 无法写入 config.user.json: {exc}"

    reload_config()
    return f"{SUCCESS_PREFIX} 模型已切换: {old_model} → {candidate}（已写入 config.user.json）"


def format_model_info() -> str:
    """格式化 ``/model`` 无参数时的 Markdown 帮助与当前模型信息。"""
    current_model = get_current_model()

    lines = [
        "## 当前模型配置",
        "",
        f"**模型**: `{current_model}`",
        "",
        "### 常用模型示例（OpenAI API）",
        "- `gpt-4o-mini`: 快速响应，成本低（推荐）",
        "- `gpt-4o`: 高质量，中等成本",
        "- `gpt-4-turbo`: 最高质量，高成本",
        "- `gpt-3.5-turbo`: 传统快速模型",
        "",
        "### 使用方式",
        "```",
        "/model               # 显示当前模型",
        "/model gpt-4o        # 切换到 gpt-4o（写入 config.user.json 并热加载）",
        "```",
        "",
        "**说明**: `/model <name>` 会持久化到 `config.user.json` 并立即生效。",
        "其他 `model.*` 字段（如 `base_url`、`temperature`）可用 `/config model` 查看，",
        "或手动编辑 `config.user.json`。",
    ]

    return "\n".join(lines)


__all__ = ["get_current_model", "switch_model", "format_model_info"]
