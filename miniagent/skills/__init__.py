"""技能系统模块（磁盘包发现、注册表、ClawHub）

技能包从 ``get_skills_root()`` 目录加载（见 ``paths``），在 ``engine.init_subsystems`` 中注册到
``DefaultSkillRegistry``，并与 ``builtin_toolboxes.BUILTIN_TOOLBOXES`` 合并后交给规划器。

ClawHub 客户端由 ``create_clawhub_client()`` 构造并注入 ``RuntimeContext.clawhub``，供工具层
搜索/安装技能时复用（``ToolContext.clawhub`` 优先）。

导出：注册表、解析/加载函数、ClawHub 工厂、技能根路径解析。

目录迁移与 wheel 发行说明见根目录 ``README``；第三方清单见 ``workspaces/skills/THIRD_PARTY_SKILLS.md``。
"""

from miniagent.skills.registry import DefaultSkillRegistry
from miniagent.skills.loader import parse_skill_md, load_skill_package, discover_skill_packages
from miniagent.skills.clawhub_client import create_clawhub_client, search_local_skills
from miniagent.skills.paths import get_skills_root

__all__ = [
    "DefaultSkillRegistry",
    "parse_skill_md",
    "load_skill_package",
    "discover_skill_packages",
    "create_clawhub_client",
    "search_local_skills",
    "get_skills_root",
]
