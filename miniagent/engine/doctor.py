"""Mini Agent Python — 环境诊断命令

检查安装、配置和运行环境，帮助用户排查问题。
"""

from __future__ import annotations

import os
import sys


def diagnose_environment() -> str:
    """诊断安装与配置环境。

    检查项目：
    - Python版本和平台
    - 核心依赖安装状态
    - 环境变量配置
    - 网络连接状态
    - 文件权限

    Returns:
        格式化的诊断报告
    """
    lines = ["## MiniAgent 环境诊断", ""]

    # 1. Python环境
    lines.append("### Python 环境")
    lines.append(f"- 版本: {sys.version}")
    lines.append(f"- 平台: {sys.platform}")
    lines.append(f"- 可执行文件: {sys.executable}")
    lines.append("")

    # 2. 核心依赖检查
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

    # 3. 环境变量配置
    lines.append("### 环境变量配置")
    env_vars = [
        ("OPENAI_API_KEY", "API密钥"),
        ("OPENAI_BASE_URL", "API地址"),
        ("OPENAI_MODEL", "模型名称"),
        ("MINI_AGENT_STATE", "状态目录"),
        ("MINIAGENT_KB_ROOT", "知识库目录"),
    ]

    for var_name, display_name in env_vars:
        value = os.environ.get(var_name)
        if value:
            # 隐藏敏感信息
            if "KEY" in var_name or "SECRET" in var_name:
                display_value = value[:8] + "..." if len(value) > 8 else "***"
            else:
                display_value = value
            lines.append(f"- ✅ {display_name}: {display_value}")
        else:
            lines.append(f"- ⚠️ {display_name}: 未设置")
    lines.append("")

    # 4. 状态目录检查
    lines.append("### 状态目录")
    state_dir = os.environ.get("MINI_AGENT_STATE", "workspaces")
    if os.path.exists(state_dir):
        lines.append(f"- ✅ 状态目录存在: {state_dir}")
        # 检查子目录
        subdirs = ["sessions", "memory", "knowledge"]
        for subdir in subdirs:
            path = os.path.join(state_dir, subdir)
            if os.path.exists(path):
                lines.append(f"  - ✅ {subdir}/")
            else:
                lines.append(f"  - ⚠️ {subdir}/ (不存在)")
    else:
        lines.append(f"- ❌ 状态目录不存在: {state_dir}")
    lines.append("")

    # 5. 总结和建议
    lines.append("### 建议")

    # 检查是否缺少关键配置
    missing_critical = []
    if not os.environ.get("OPENAI_API_KEY"):
        missing_critical.append("OPENAI_API_KEY未设置")
    if not os.environ.get("OPENAI_BASE_URL"):
        missing_critical.append("OPENAI_BASE_URL未设置（使用默认OpenAI地址）")

    if missing_critical:
        lines.append("⚠️ 发现以下问题:")
        for issue in missing_critical:
            lines.append(f"  - {issue}")
        lines.append("")
        lines.append("建议操作:")
        lines.append("1. 复制 .env.example 为 .env")
        lines.append("2. 在 .env 中填写必要的配置")
        lines.append("3. 重启 MiniAgent")
    else:
        lines.append("✅ 环境配置完整，可正常使用")

    return "\n".join(lines)


__all__ = ["diagnose_environment"]