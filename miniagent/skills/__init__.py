"""Mini Agent Python — 技能系统模块

技能包从磁盘目录加载（含 ``manifest`` / Markdown），在 ``engine.init_subsystems`` 中注册到
``DefaultSkillRegistry``；ClawHub 客户端由 ``create_clawhub_client()`` 构造并注入
``RuntimeContext.clawhub``，供工具层搜索/安装技能时复用。

导出技能注册表、加载器与 ClawHub 客户端工厂。
"""

from miniagent.skills.registry import DefaultSkillRegistry
from miniagent.skills.loader import parse_skill_md, load_skill_package, discover_skill_packages
from miniagent.skills.clawhub_client import create_clawhub_client, search_local_skills

__all__ = [
    "DefaultSkillRegistry",
    "parse_skill_md",
    "load_skill_package",
    "discover_skill_packages",
    "create_clawhub_client",
    "search_local_skills",
]
