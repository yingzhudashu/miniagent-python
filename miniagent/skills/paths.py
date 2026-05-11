"""技能根目录解析（与引擎加载、ClawHub 安装共用）。

旧版若将技能装在仓库根目录 ``skills/``，请整体移动到 ``workspaces/skills/``，
或设置环境变量 ``MINI_AGENT_SKILLS`` 指向原目录；引擎不会自动合并两处扫描。

用户说明见根目录 ``README``「技能目录迁移」。
"""

from __future__ import annotations

import os
from pathlib import Path


def get_skills_root() -> str:
    """返回技能包根目录。

    优先级：
    1. 环境变量 ``MINI_AGENT_SKILLS``
    2. 本包所在仓库下的 ``workspaces/skills``（相对 ``miniagent/skills/paths.py`` 定位仓库根）
    """
    env = os.environ.get("MINI_AGENT_SKILLS")
    if env:
        return env

    here = Path(__file__).resolve().parent
    repo_root = here.parent.parent
    return str(repo_root / "workspaces" / "skills")


__all__ = ["get_skills_root"]
