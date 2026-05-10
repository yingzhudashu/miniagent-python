"""Engine — 欢迎界面

拆分自 unified.py。

职责：
- 版本号读取（从 pyproject.toml）
- 欢迎信息打印
- 会话显示名称获取
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def get_version() -> str:
    """从 pyproject.toml 读取版本号。"""
    try:
        import tomllib

        pyproject = Path(__file__).parent.parent.parent / "pyproject.toml"
        with open(pyproject, "rb") as f:
            data = tomllib.load(f)
        return data.get("project", {}).get("version", "0.1.0")
    except Exception:
        return "0.1.0"


def get_session_display(session_manager: Any, active_session_id: str) -> str:
    """获取当前会话显示名称。"""
    if not session_manager or not active_session_id:
        return "未初始化"
    return session_manager.get_session_display_name(active_session_id)


def print_welcome(
    registry: Any,
    skill_registry: Any,
    model: str,
    active_profile: str,
    session_manager: Any,
    active_session_id: str,
    feishu_enabled: bool = False,
) -> None:
    """简洁美观的启动欢迎界面。

    Args:
        registry: 工具注册表
        skill_registry: 技能注册表
        model: 当前模型名称
        active_profile: 当前模型预设
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
    print(f"  📡 {model}  ({active_profile})")
    print(f"  🔧 {tool_count} tools  ·  📦 {skill_count} skills  ·  {feishu_label}")
    print(f"  💼 {display_name}")
    print()


__all__ = ["get_version", "get_session_display", "print_welcome"]
