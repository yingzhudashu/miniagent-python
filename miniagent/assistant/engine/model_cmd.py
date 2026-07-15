"""Mini Agent Python — 模型管理命令

显示和切换当前使用的 LLM 模型（写入 config.user.json）。
切换后会调用 ``reload_config()``，与配置热更新监听（``config_watch``）行为一致。
"""

from __future__ import annotations

import json

from miniagent.agent.types.error_prefix import ERROR_PREFIX, SUCCESS_PREFIX
from miniagent.assistant.infrastructure.atomic_json import atomic_dump_json
from miniagent.assistant.infrastructure.json_config import (
    get_config,
    get_user_config_path,
    reload_config,
)


def switch_model_profile(
    profile: str,
    *,
    role: str = "default",
    descriptor: object | None = None,
) -> str:
    """Persist one v3 role binding and optionally its dynamic model descriptor."""
    candidate = profile.strip()
    if not candidate:
        return f"{ERROR_PREFIX} 模型 profile 不能为空"
    if role not in ("default", "reasoning", "fast", "vision"):
        return f"{ERROR_PREFIX} 未知模型角色: {role}"
    user_path = get_user_config_path()
    try:
        existing = json.loads(user_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return f"{ERROR_PREFIX} 无法读取 config.user.json: {error}"
    if not isinstance(existing, dict):
        return f"{ERROR_PREFIX} config.user.json 根节点必须是 JSON 对象"
    llm = existing.get("llm")
    if not isinstance(llm, dict):
        return (
            f"{ERROR_PREFIX} 当前仍是 v2 模型配置；请先运行 "
            "`python -m miniagent migrate-config --write`"
        )
    roles = llm.setdefault("roles", {})
    if not isinstance(roles, dict):
        return f"{ERROR_PREFIX} llm.roles 必须是对象"
    old = str(roles.get(role) or "")
    roles[role] = candidate
    if descriptor is not None:
        models = llm.setdefault("models", {})
        if not isinstance(models, dict):
            return f"{ERROR_PREFIX} llm.models 必须是对象"
        if candidate not in models:
            capabilities = getattr(descriptor, "capabilities", None)
            models[candidate] = {
                "provider": getattr(descriptor, "provider", ""),
                "model": getattr(descriptor, "model", candidate),
                "api": getattr(descriptor, "api", "openai_chat"),
                "context_window": getattr(descriptor, "context_window", 128_000),
                "max_output_tokens": getattr(descriptor, "max_output_tokens", 4_096),
                "capabilities": {
                    "tools": bool(getattr(capabilities, "tools", True)),
                    "vision": bool(getattr(capabilities, "vision", False)),
                    "reasoning": bool(getattr(capabilities, "reasoning", False)),
                    "structured_output": bool(
                        getattr(capabilities, "structured_output", True)
                    ),
                },
            }
    try:
        atomic_dump_json(user_path, existing, indent=2, ensure_ascii=False)
    except OSError as error:
        return f"{ERROR_PREFIX} 无法写入 config.user.json: {error}"
    reload_config()
    return f"{SUCCESS_PREFIX} {role} 模型已切换: {old or '未设置'} → {candidate}"


def get_current_model() -> str:
    """读取当前生效的 ``model.model`` 配置值。

    合并顺序为 ``config.user.json`` 覆盖包内 defaults。
    空字符串或非字符串异常值回退到 ``gpt-4o-mini``。
    """
    from miniagent.assistant.infrastructure.json_config import get_user_config_section

    user_llm = get_user_config_section("llm")
    if user_llm:
        roles = user_llm.get("roles") if isinstance(user_llm, dict) else None
        models = user_llm.get("models") if isinstance(user_llm, dict) else None
        if isinstance(roles, dict) and isinstance(models, dict):
            profile = str(roles.get("default") or "")
            entry = models.get(profile)
            if isinstance(entry, dict) and entry.get("model"):
                return str(entry["model"])
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

    from miniagent.assistant.infrastructure.json_config import get_user_config_section

    if get_user_config_section("llm"):
        return switch_model_profile(candidate)
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
    from miniagent.assistant.infrastructure.json_config import get_user_config_section

    llm = get_user_config_section("llm")
    if llm:
        roles_raw = llm.get("roles")
        models_raw = llm.get("models")
        roles = roles_raw if isinstance(roles_raw, dict) else {}
        models = models_raw if isinstance(models_raw, dict) else {}
        lines = ["## LLM 模型角色", ""]
        for role in ("default", "reasoning", "fast", "vision"):
            profile = str(roles.get(role) or roles.get("default") or "未配置")
            entry = models.get(profile, {})
            model = entry.get("model", "动态/内置目录") if isinstance(entry, dict) else "未知"
            lines.append(f"- **{role}**: `{profile}` → `{model}`")
        lines.extend(
            [
                "",
                "TUI 使用 `Ctrl+P` 选择默认回答模型；命令行使用 `/model <profile>`。",
                "provider、凭据与动态目录说明见 `docs/LLM_PROVIDERS.md`。",
            ]
        )
        return "\n".join(lines)
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


__all__ = [
    "format_model_info",
    "get_current_model",
    "switch_model",
    "switch_model_profile",
]
