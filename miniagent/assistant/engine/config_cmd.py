"""Mini Agent Python — 配置查看命令

显示当前配置信息，支持查看整体配置概览或特定配置部分。
仅查看，不修改（避免运行时状态混乱）。
"""

from __future__ import annotations

import json
from typing import Any

from miniagent.agent.types.error_prefix import WARNING_PREFIX
from miniagent.assistant.infrastructure.json_config import _packaged_defaults_path

# guide 不可读时的兜底；正常以包内 defaults 的 `_config_guide` 为准
_USER_SECTIONS_FALLBACK = frozenset({
    "secrets", "model", "paths", "features", "embedding", "timezone",
    "session", "mcp", "security", "scheduled_tasks", "scheduled_tools",
    "knowledge", "agent", "cli", "feishu", "debug", "agent_html",
})

_ADVANCED_SECTIONS_FALLBACK = frozenset({"memory", "dream", "trace", "self_optimization"})

_SENSITIVE_KEYS = frozenset({
    "api_key", "secret", "password", "token", "credential",
    "openai_api_key", "tavily_api_key", "feishu_app_secret",
})

_SENSITIVE_PARTS = frozenset({"key", "secret", "password", "token", "credential"})

_MAX_NEST_DEPTH = 6
_MAX_LIST_ITEMS = 8
_OVERVIEW_MAX_KEYS = 4
_OVERVIEW_LIST_PREVIEW = 3

_EMPTY_SECTION_HINT = "- _（当前无配置项，使用默认或未覆盖）_"


def _load_config_guide() -> dict[str, Any]:
    """读取包内 defaults 的 ``_config_guide``；失败时返回空 dict。"""
    try:
        with open(_packaged_defaults_path(), encoding="utf-8") as defaults_file:
            data = json.load(defaults_file)
        guide = data.get("_config_guide", {})
        return guide if isinstance(guide, dict) else {}
    except (OSError, json.JSONDecodeError, TypeError):
        return {}


def _user_sections(guide: dict[str, Any] | None = None) -> frozenset[str]:
    """User 层 section 集合；优先 ``_config_guide.user_sections``。"""
    g = guide if guide is not None else _load_config_guide()
    raw = g.get("user_sections")
    if isinstance(raw, list) and raw:
        return frozenset(str(s) for s in raw)
    return _USER_SECTIONS_FALLBACK


def _advanced_sections(guide: dict[str, Any] | None = None) -> frozenset[str]:
    """Advanced 层 section 集合；优先 ``_config_guide.advanced_sections``。"""
    g = guide if guide is not None else _load_config_guide()
    raw = g.get("advanced_sections")
    if isinstance(raw, list) and raw:
        return frozenset(str(s) for s in raw)
    return _ADVANCED_SECTIONS_FALLBACK


def _is_sensitive_key(key: str) -> bool:
    """判断配置键是否应按敏感信息脱敏（按 ``_`` 分段匹配，避免误伤 ``keyword_*``）。"""
    lowered = key.lower()
    if lowered in _SENSITIVE_KEYS:
        return True
    return any(part in _SENSITIVE_PARTS for part in lowered.split("_"))


def _mask_sensitive(key: str, value: Any) -> str:
    """将敏感配置值脱敏为前缀或 ``***``。"""
    if _is_sensitive_key(key):
        if isinstance(value, str) and len(value) > 8:
            return value[:8] + "..."
        return "***"
    return str(value)


def _indent_prefix(indent: int) -> str:
    return "  " * indent


def _append_value_lines(
    lines: list[str],
    key: str,
    value: Any,
    *,
    indent: int = 0,
    depth: int = 0,
    max_list_items: int = _MAX_LIST_ITEMS,
) -> None:
    """将单个配置项递归格式化为 Markdown 列表行（详情模式）。"""
    prefix = _indent_prefix(indent)

    if isinstance(value, dict):
        lines.append(f"{prefix}- `{key}`:")
        if not value:
            lines.append(f"{prefix}  - _（空）_")
            return
        if depth >= _MAX_NEST_DEPTH:
            lines.append(f"{prefix}  - `{len(value)} 子项`")
            return
        for sub_k, sub_v in sorted(value.items()):
            _append_value_lines(
                lines,
                sub_k,
                sub_v,
                indent=indent + 1,
                depth=depth + 1,
                max_list_items=max_list_items,
            )
        return

    if isinstance(value, list):
        if not value:
            lines.append(f"{prefix}- `{key}`: `0 项`")
            return
        lines.append(f"{prefix}- `{key}`: `{len(value)} 项`")
        for index, item in enumerate(value):
            if index >= max_list_items:
                lines.append(f"{prefix}  - ... (共 {len(value)} 项)")
                break
            item_prefix = f"{prefix}  "
            if isinstance(item, dict):
                lines.append(f"{item_prefix}- `[{index}]`:")
                if not item:
                    lines.append(f"{item_prefix}  - _（空）_")
                elif depth >= _MAX_NEST_DEPTH:
                    lines.append(f"{item_prefix}  - `{len(item)} 子项`")
                else:
                    for sub_k, sub_v in sorted(item.items()):
                        _append_value_lines(
                            lines,
                            sub_k,
                            sub_v,
                            indent=indent + 2,
                            depth=depth + 1,
                            max_list_items=max_list_items,
                        )
            elif isinstance(item, list):
                lines.append(f"{item_prefix}- `[{index}]`: `{len(item)} 项`")
            else:
                lines.append(f"{item_prefix}- `[{index}]`: `{item}`")
        return

    lines.append(f"{prefix}- `{key}`: `{_mask_sensitive(key, value)}`")


def _summarize_value(key: str, value: Any, *, max_list_preview: int = _OVERVIEW_LIST_PREVIEW) -> str:
    """概览模式下对配置值做简短摘要。"""
    if isinstance(value, dict):
        return f"{len(value)} 子项"
    if isinstance(value, list):
        if not value:
            return "0 项"
        if (
            len(value) <= max_list_preview
            and all(not isinstance(item, (dict, list)) for item in value)
        ):
            preview = ", ".join(str(item) for item in value)
            return f"{len(value)} 项: {preview}"
        return f"{len(value)} 项"
    return _mask_sensitive(key, value)


def format_config_info(section: str | None = None) -> str:
    """格式化配置信息为 Markdown 文本。

    Args:
        section: 配置节名（如 ``model``、``memory``）。为 ``None`` 时返回 User 层概览。

    Returns:
        供 CLI / 飞书展示的 Markdown 字符串；未知或空节返回警告文案。
    """
    from miniagent.assistant.infrastructure.json_config import get_config_section

    guide = _load_config_guide()
    user_layer = _user_sections(guide)
    advanced_layer = _advanced_sections(guide)

    if section:
        data = get_config_section(section)
        if not data:
            return f"{WARNING_PREFIX} 配置部分 `{section}` 不存在或为空"

        layer_hint = ""
        if section in advanced_layer:
            layer_hint = "（Advanced 运维默认值，一般无需写入 config.user.json）"
        elif section in user_layer:
            layer_hint = "（User 层，可在 config.user.json 中覆盖）"

        lines = [f"## 配置: {section}{layer_hint}", ""]
        for k, v in sorted(data.items()):
            _append_value_lines(lines, k, v)

        lines.append("")
        lines.append("💡 修改配置请编辑 `config.user.json`（参考包内 `miniagent/resources/config.defaults.json` 的分层结构）")
        return "\n".join(lines)

    user_sections = guide.get("user_sections") or sorted(_USER_SECTIONS_FALLBACK)

    lines = ["## MiniAgent 配置概览", "", "### User 层（常用）", ""]

    for sec in user_sections:
        if sec not in user_layer:
            continue
        data = get_config_section(sec)
        lines.append(f"#### {sec}")
        if not data:
            lines.append(_EMPTY_SECTION_HINT)
            lines.append("")
            continue
        count = 0
        for k, v in sorted(data.items()):
            if count >= _OVERVIEW_MAX_KEYS:
                lines.append(f"- ... (共 {len(data)} 项)")
                break
            lines.append(f"- `{k}`: `{_summarize_value(k, v)}`")
            count += 1
        lines.append("")

    lines.append("### 查看完整配置")
    lines.append("使用 `/config [section]` 查看特定配置部分，例如：")
    lines.append("- `/config model` - 模型配置")
    lines.append("- `/config paths` - 路径配置")
    lines.append("- `/config feishu` - 飞书渠道")
    lines.append("- `/config memory` - 记忆运维（Advanced）")

    lines.append("")
    lines.append("### 配置文件")
    lines.append("- `miniagent/resources/config.defaults.json` - 包内默认配置（含 User/Advanced 分层说明）")
    lines.append("- `config.user.json` - 用户覆盖（仅需填写个性化项与 secrets）")

    return "\n".join(lines)


__all__ = ["format_config_info"]
