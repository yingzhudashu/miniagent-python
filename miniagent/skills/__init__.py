"""Mini Agent Python — 技能系统模块

导出技能注册表、加载器和 ClawHub 客户端。
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
