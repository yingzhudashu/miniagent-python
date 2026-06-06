"""Mini Agent Python — 环境诊断命令

检查安装、配置和运行环境，帮助用户排查问题。
"""

from __future__ import annotations

import sys

from miniagent.infrastructure.json_config import get_config


def diagnose_environment() -> str:
    """诊断安装与配置环境。"""
    import os

    lines = ["## MiniAgent 环境诊断", ""]

    lines.append("### Python 环境")
    lines.append(f"- 版本: {sys.version}")
    lines.append(f"- 平台: {sys.platform}")
    lines.append(f"- 可执行文件: {sys.executable}")
    lines.append("")

    lines.append("### 核心依赖")
    dependencies = [
        ("openai", "OpenAI SDK"),
        ("prompt_toolkit", "CLI界面"),
        ("rich", "Markdown渲染"),
        ("yaml", "配置解析"),
        ("lark_oapi", "飞书集成"),
    ]

    for module_name, display_name in dependencies:
        try:
            __import__(module_name)
            lines.append(f"- ✅ {display_name}: 已安装")
        except ImportError:
            lines.append(f"- ❌ {display_name}: 未安装")
    lines.append("")

    lines.append("### JSON 配置（config.user.json）")
    api_key = get_config("secrets.openai_api_key", "")
    if api_key:
        display_value = str(api_key)[:8] + "..." if len(str(api_key)) > 8 else "***"
        lines.append(f"- ✅ API 密钥: {display_value}")
    else:
        lines.append("- ⚠️ secrets.openai_api_key: 未设置")

    lines.append(f"- 模型: {get_config('model.model', 'gpt-4o-mini')}")
    lines.append(f"- API 地址: {get_config('model.base_url', 'https://api.openai.com/v1')}")
    lines.append("")

    lines.append("### 状态目录")
    from miniagent.infrastructure.paths import resolve_state_dir

    state_dir = resolve_state_dir()
    if os.path.exists(state_dir):
        lines.append(f"- ✅ 状态目录存在: {state_dir}")
        for subdir in ("sessions", "memory", "knowledge"):
            path = os.path.join(state_dir, subdir)
            if os.path.exists(path):
                lines.append(f"  - ✅ {subdir}/")
            else:
                lines.append(f"  - ⚠️ {subdir}/ (不存在)")
    else:
        lines.append(f"- ❌ 状态目录不存在: {state_dir}")
    lines.append("")

    lines.append("### 建议")
    missing_critical = []
    if not api_key:
        missing_critical.append("secrets.openai_api_key 未设置")

    if missing_critical:
        lines.append("⚠️ 发现以下问题:")
        for issue in missing_critical:
            lines.append(f"  - {issue}")
        lines.append("")
        lines.append("建议操作:")
        lines.append("1. 复制 config.defaults.json 为 config.user.json")
        lines.append("2. 在 secrets 中填写 openai_api_key")
    else:
        lines.append("✅ 基本配置检查通过")

    return "\n".join(lines)


__all__ = ["diagnose_environment"]
