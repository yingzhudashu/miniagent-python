"""Engine — 欢迎界面

拆分自 unified.py。

职责：
- 版本号：与 ``miniagent.__version__`` 一致（``pyproject.toml`` 使用 dynamic version，勿再读静态字段）
- 欢迎信息打印
- 会话显示名称获取

文档维护清单要求版本号与 ``CHANGELOG`` / ``docs/ENGINEERING.md`` 一致。
"""

from __future__ import annotations

import os
from typing import Any


def get_version() -> str:
    """返回发布版本号，与 ``miniagent.__version__`` 及 setuptools ``dynamic.version`` 同源。"""
    from miniagent import __version__

    return __version__


def get_session_display(session_manager: Any, active_session_id: str) -> str:
    """获取当前会话显示名称。"""
    if not session_manager or not active_session_id:
        return "未初始化"
    return session_manager.get_session_display_name(active_session_id)


def print_welcome(
    registry: Any,
    skill_registry: Any,
    model: str,
    session_manager: Any,
    active_session_id: str,
    feishu_enabled: bool = False,
) -> None:
    """简洁美观的启动欢迎界面。

    Args:
        registry: 工具注册表
        skill_registry: 技能注册表
        model: 当前模型名称
        session_manager: 会话管理器
        active_session_id: 当前会话 ID
        feishu_enabled: 飞书是否已启用
    """
    version = get_version()
    tool_count = len(registry.list())
    skill_count = len(skill_registry.get_all())
    feishu_label = "飞书" if feishu_enabled else "待命"
    display_name = get_session_display(session_manager, active_session_id)

    print()
    print(f"  🤖 Mini Agent  v{version}")
    print(f"  📡 {model}")
    print(f"  🔧 {tool_count} tools  ·  📦 {skill_count} skills  ·  {feishu_label}")
    print(f"  💼 {display_name}")
    hint_on = os.environ.get("MINIAGENT_WELCOME_CLI_HINT", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )
    if hint_on:
        try:
            import rich.markdown  # noqa: F401
        except ImportError:
            print(
                '  💡 提示: pip install -e ".[cli]" 可在终端渲染 Assistant 的 Markdown（表格/加粗等）。'
            )
    print()


__all__ = ["get_version", "get_session_display", "print_welcome"]
