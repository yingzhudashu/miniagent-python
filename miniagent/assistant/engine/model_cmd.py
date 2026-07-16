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
    except FileNotFoundError:
        existing = {}
    except (OSError, json.JSONDecodeError) as error:
        return f"{ERROR_PREFIX} 无法读取 config.user.json: {error}"
    if not isinstance(existing, dict):
        return f"{ERROR_PREFIX} config.user.json 根节点必须是 JSON 对象"
    llm = existing.setdefault("llm", {})
    if not isinstance(llm, dict):
        return f"{ERROR_PREFIX} llm 配置节必须是对象"
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
    """Return the model id selected by the effective default role."""
    from miniagent.llm.factory import effective_llm_config

    llm = effective_llm_config(get_config)
    roles_value = llm.get("roles")
    models_value = llm.get("models")
    roles: dict[str, object] = roles_value if isinstance(roles_value, dict) else {}
    models: dict[str, object] = models_value if isinstance(models_value, dict) else {}
    profile = str(roles.get("default") or "primary")
    entry = models.get(profile, {})
    return str(entry.get("model") or profile) if isinstance(entry, dict) else profile


def switch_model(new_model: str) -> str:
    """Switch the default role to a configured model profile."""
    candidate = new_model.strip()
    if not candidate:
        return f"{ERROR_PREFIX} 模型 profile 不能为空"
    return switch_model_profile(candidate)


def format_model_info() -> str:
    """格式化 ``/model`` 无参数时的 Markdown 帮助与当前模型信息。"""
    from miniagent.llm.factory import effective_llm_config

    llm = effective_llm_config(get_config)
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


__all__ = [
    "format_model_info",
    "get_current_model",
    "switch_model",
    "switch_model_profile",
]
