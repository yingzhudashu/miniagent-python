"""Engine — 欢迎界面

拆分自 unified.py。

职责：
- 版本号：与 ``miniagent.__version__`` 一致（``pyproject.toml`` 使用 dynamic version，勿再读静态字段）
- 欢迎信息打印
- 会话显示名称获取

文档维护清单要求版本号与 ``CHANGELOG`` / ``docs/ENGINEERING.md`` 一致。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from miniagent.infrastructure.json_config import get_config


def get_version() -> str:
    """返回发布版本号，与 ``miniagent.__version__`` 及 setuptools ``dynamic.version`` 同源。"""
    from miniagent import __version__

    return __version__


def get_session_display(session_manager: Any, active_session_id: str) -> str:
    """获取当前会话显示名称。"""
    if not session_manager or not active_session_id:
        return "未初始化"
    return session_manager.get_session_display_name(active_session_id)


@dataclass(frozen=True)
class SkillDisplayCounts:
    """欢迎界面用的技能分层统计。

    Attributes:
        global_packages: 父级技能包数（``workspaces/skills``，scope=global）
        session_skills: 当前会话子技能数（``workspaces/sessions/<id>/skills``，scope=session:<id>）
    """

    global_packages: int
    session_skills: int


def _packages_for_scope(skill_registry: Any, scope: str) -> list[Any]:
    """返回指定 scope 下的技能包列表。"""
    packages = skill_registry.get_packages()
    return [pkg for pkg in packages if (pkg.scope or "global") == scope]


def compute_skill_display_counts(
    skill_registry: Any,
    active_session_id: str | None = None,
) -> SkillDisplayCounts:
    """按父级（全局包）与会话子技能汇总统计。

    - 父级：``workspaces/skills`` 下的 ``SkillPackage``（scope=global）
    - 子技能：各会话目录 ``workspaces/sessions/<id>/skills`` 下的包（scope=session:<id>）；
      欢迎界面仅展示当前 ``active_session_id`` 对应会话的数量。
    """
    global_pkgs = _packages_for_scope(skill_registry, "global")
    session_scope = f"session:{active_session_id}" if active_session_id else None
    session_pkgs = _packages_for_scope(skill_registry, session_scope) if session_scope else []

    return SkillDisplayCounts(
        global_packages=len(global_pkgs),
        session_skills=len(session_pkgs),
    )


def _skill_count_label(count: int, scope: str) -> str:
    """格式化为 ``N global/session skill(s)``。"""
    noun = "skill" if count == 1 else "skills"
    return f"{count} {scope} {noun}"


def format_skill_display_label(counts: SkillDisplayCounts) -> str:
    """将分层统计格式化为欢迎行中的 skills 片段。"""
    return (
        f"{_skill_count_label(counts.global_packages, 'global')}"
        f" · {_skill_count_label(counts.session_skills, 'session')}"
    )


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
    skill_label = format_skill_display_label(
        compute_skill_display_counts(skill_registry, active_session_id)
    )
    feishu_label = "飞书" if feishu_enabled else "待命"
    display_name = get_session_display(session_manager, active_session_id)

    print()
    print(f"  🤖 Mini Agent  v{version}")
    print(f"  📡 {model}")
    print(f"  🔧 {tool_count} tools  ·  📦 {skill_label}  ·  {feishu_label}")
    print(f"  💼 {display_name}")
    hint_on = get_config("cli.welcome_hint", True)
    if hint_on:
        try:
            import rich.markdown  # noqa: F401
        except ImportError:
            print(
                '  💡 提示: pip install -e ".[cli]" 可在终端渲染 Assistant 的 Markdown（表格/加粗等）。'
            )
    print()


__all__ = [
    "SkillDisplayCounts",
    "compute_skill_display_counts",
    "format_skill_display_label",
    "get_session_display",
    "get_version",
    "print_welcome",
]
