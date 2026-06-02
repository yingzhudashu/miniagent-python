"""Mini Agent Python — 配置查看命令

显示当前配置信息，支持查看整体配置概览或特定配置部分。
仅查看，不修改（避免运行时状态混乱）。
"""

from __future__ import annotations

from typing import Any


def format_config_info(section: str | None = None) -> str:
    """格式化配置信息显示。

    Args:
        section: 配置部分名称（如 "model"），None时显示概览

    Returns:
        格式化的配置信息文本
    """
    from miniagent.infrastructure.json_config import get_config_section

    # 敏感字段列表（需要隐藏）
    SENSITIVE_KEYS = frozenset([
        "api_key", "secret", "password", "token", "credential",
        "openai_api_key", "tavily_api_key", "feishu_app_secret",
    ])

    def _mask_sensitive(key: str, value: Any) -> str:
        """隐藏敏感信息。"""
        if key.lower() in SENSITIVE_KEYS or any(s in key.lower() for s in ["key", "secret", "password", "token"]):
            if isinstance(value, str) and len(value) > 8:
                return value[:8] + "..."
            return "***"
        return str(value)

    if section:
        # 显示特定配置部分
        data = get_config_section(section)
        if not data:
            return f"⚠️ 配置部分 `{section}` 不存在或为空"

        lines = [f"## 配置: {section}", ""]
        for k, v in sorted(data.items()):
            masked_value = _mask_sensitive(k, v)
            # 格式化显示
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
        lines.append(f"💡 提示：使用环境变量覆盖配置（如 MINIAGENT_{section.upper()}_XXX）")
        return "\n".join(lines)

    # 显示配置概览
    lines = ["## MiniAgent 配置概览", ""]
    sections = ["model", "paths", "features", "cli", "background_tasks"]

    for sec in sections:
        data = get_config_section(sec)
        if not data:
            continue
        lines.append(f"### {sec}")
        count = 0
        for k, v in sorted(data.items()):
            if count >= 5:  # 每部分最多显示5项
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
    lines.append("使用 `/config <section>` 查看特定配置部分的完整信息：")
    lines.append("- `/config model` - 模型配置")
    lines.append("- `/config paths` - 路径配置")
    lines.append("- `/config features` - 功能开关")
    lines.append("- `/config cli` - CLI配置")
    lines.append("- `/config background_tasks` - 后台任务配置")

    lines.append("")
    lines.append("### 配置文件位置")
    lines.append("- `config.defaults.json` - 默认配置（随代码发布）")
    lines.append("- `config.user.json` - 用户配置（覆盖默认值）")
    lines.append("- 环境变量 `MINIAGENT_*` - 最高优先级")

    return "\n".join(lines)


__all__ = ["format_config_info"]