"""技能根目录解析（与引擎加载、ClawHub 安装共用）。

迁移说明：若技能曾安装在仓库根目录 ``skills/``，请整体移动到 ``workspaces/skills/``，
或设置配置 ``paths.skills_dir`` 指向原目录；引擎不会自动合并两处扫描。

用户说明见根目录 ``README``「技能目录迁移」。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from miniagent.infrastructure.json_config import get_config

_logger = logging.getLogger(__name__)


def _repo_root() -> Path:
    """相对 ``miniagent/skills/paths.py`` 定位仓库根目录。"""
    return Path(__file__).resolve().parent.parent.parent


def get_skills_root() -> str:
    """返回技能包根目录。

    优先级：
    1. 配置 ``paths.skills_dir``
    2. 本包所在仓库下的 ``workspaces/skills``（相对 ``miniagent/skills/paths.py`` 定位仓库根）
    """
    env = get_config("paths.skills_dir", None)
    if env:
        return env

    return str(_repo_root() / "workspaces" / "skills")


def get_session_skills_dir(session_id: str) -> str:
    """返回指定会话的技能目录路径（若存在）。

    Args:
        session_id: 会话 ID

    Returns:
        会话技能目录路径，格式为 ``workspaces/sessions/<id>/skills/``
    """
    return str(_repo_root() / "workspaces" / "sessions" / session_id / "skills")


def get_all_skill_roots(*, include_sessions: bool = True) -> list[str]:
    """返回所有需要扫描的技能根目录列表。

    包含：
    1. 主技能根目录（``get_skills_root()``）
    2. 所有已存在会话的 skills 子目录（若 ``include_sessions=True``）

    同一技能名在多个根中出现时，主根优先（先扫描的优先注册）。
    """
    roots: list[str] = [get_skills_root()]

    if include_sessions:
        sessions_dir = _get_sessions_dir()
        if os.path.isdir(sessions_dir):
            for entry in sorted(os.listdir(sessions_dir)):
                if entry.startswith("."):
                    continue
                session_skills = os.path.join(sessions_dir, entry, "skills")
                if os.path.isdir(session_skills) and os.listdir(session_skills):
                    roots.append(session_skills)

    return roots


def _get_sessions_dir() -> str:
    """返回会话根目录路径。"""
    return str(_repo_root() / "workspaces" / "sessions")


def resolve_scope_for_root(root: str) -> str:
    """根据技能根目录路径判断 scope：``"global"`` 或 ``"session:<id>"``。

    Args:
        root: 技能根目录路径

    Returns:
        scope 字符串，如 ``"global"`` 或 ``"session:abc123"``
    """
    sessions_dir = _get_sessions_dir()
    if root.startswith(sessions_dir):
        # workspaces/sessions/<id>/skills → session:<id>
        parts = Path(root).parts
        try:
            sessions_idx = parts.index("sessions")
            if sessions_idx + 1 < len(parts):
                return f"session:{parts[sessions_idx + 1]}"
        except ValueError as e:
            _logger.debug("路径不含sessions目录: %s", e)
    return "global"


__all__ = ["get_skills_root", "get_session_skills_dir", "get_all_skill_roots", "resolve_scope_for_root"]
