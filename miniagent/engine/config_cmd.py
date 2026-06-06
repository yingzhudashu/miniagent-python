"""Mini Agent Python — 配置查看命令

显示当前配置信息，支持查看整体配置概览或特定配置部分。
仅查看，不修改（避免运行时状态混乱）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_USER_SECTIONS = frozenset({
    "secrets", "model", "paths", "features", "embedding", "timezone",
    "session", "mcp", "security", "scheduled_tasks", "scheduled_tools",
    "knowledge", "agent", "cli", "feishu", "debug",
})

_ADVANCED_SECTIONS = frozenset({"memory", "dream", "trace", "self_optimization"})


def _load_config_guide() -> dict[str, Any]:
    try:
        defaults_path = Path(__file__).parent.parent.parent / "config.defaults.json"
        data = json.loads(defaults_path.read_text(encoding="utf-8"))
        guide = data.get("_config_guide", {})
        return guide if isinstance(guide, dict) else {}
    except Exception:
        return {}


def format_config_info(section: str | None = None) -> str:
    """格式化配置信息显示。"""
    from miniagent.infrastructure.json_config import get_config_section

    SENSITIVE_KEYS = frozenset([
        "api_key", "secret", "password", "token", "credential",
        "openai_api_key", "tavily_api_key", "feishu_app_secret",
    ])

    def _mask_sensitive(key: str, value: Any) -> str:
        if key.lower() in SENSITIVE_KEYS or any(s in key.lower() for s in ["key", "secret", "password", "token"]):
            if isinstance(value, str) and len(value) > 8:
                return value[:8] + "..."
            return "***"
        return str(value)

    if section:
        data = get_config_section(section)
        if not data:
            return f"⚠️ 配置部分 `{section}` 不存在或为空"

        layer_hint = ""
        if section in _ADVANCED_SECTIONS:
            layer_hint = "（Advanced 运维默认值，一般无需写入 config.user.json）"
        elif section in _USER_SECTIONS:
            layer_hint = "（User 层，可在 config.user.json 中覆盖）"

        lines = [f"## 配置: {section}{layer_hint}", ""]
        for k, v in sorted(data.items()):
            masked_value = _mask_sensitive(k, v)
            if isinstance(v, dict):
                lines.append(f"- `{k}`:")
                for sub_k, sub_v in sorted(v.items()):
                    masked_sub = _mask_sensitive(sub_k, sub_v)
                    lines.append(f"  - `{sub_k}`: `{masked_sub}`")
            elif isinstance(v, list):
                lines.append(f"- `{k}`: `{len(v)} 项`")
            else:
                lines.append(f"- `{k}`: `{masked_value}`")

        lines.append("")
        lines.append("💡 修改配置请编辑 `config.user.json`（参考 `config.defaults.json` 分层结构）")
        return "\n".join(lines)

    guide = _load_config_guide()
    user_sections = guide.get("user_sections") or sorted(_USER_SECTIONS)

    lines = ["## MiniAgent 配置概览", "", "### User 层（常用）", ""]

    for sec in user_sections:
        if sec not in _USER_SECTIONS:
            continue
        data = get_config_section(sec)
        if not data:
            continue
        lines.append(f"#### {sec}")
        count = 0
        for k, v in sorted(data.items()):
            if count >= 4:
                lines.append(f"- ... (共 {len(data)} 项)")
                break
            masked_value = _mask_sensitive(k, v)
            if isinstance(v, dict):
                lines.append(f"- `{k}`: `{len(v)} 子项`")
            elif isinstance(v, list):
                lines.append(f"- `{k}`: `{len(v)} 项`")
            else:
                lines.append(f"- `{k}`: `{masked_value}`")
            count += 1
        lines.append("")

    lines.append("### 查看完整配置")
    lines.append("使用 `/config <section>` 查看特定配置部分，例如：")
    lines.append("- `/config model` - 模型配置")
    lines.append("- `/config paths` - 路径配置")
    lines.append("- `/config feishu` - 飞书渠道")
    lines.append("- `/config memory` - 记忆运维（Advanced）")

    lines.append("")
    lines.append("### 配置文件")
    lines.append("- `config.defaults.json` - 默认配置（含 User/Advanced 分层说明）")
    lines.append("- `config.user.json` - 用户覆盖（仅需填写个性化项与 secrets）")

    return "\n".join(lines)


__all__ = ["format_config_info"]
