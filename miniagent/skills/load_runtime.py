"""技能包加载到注册表的共用逻辑（启动与 refresh 共用）。"""

from __future__ import annotations

import os
from typing import Any

from miniagent.infrastructure.logger import get_logger
from miniagent.skills.loader import discover_skill_packages, load_skill_package
from miniagent.skills.paths import get_skills_root
from miniagent.types.skill import Skill, SkillPackage

_logger = get_logger(__name__)


def _register_package_tools(
    registry: Any,
    pkg: SkillPackage,
) -> tuple[list[str], list[str]]:
    """将技能包内工具注册到主工具注册表（内置同名则跳过）。

    Returns:
        (added_tool_names, skipped_tool_names)
    """
    added: list[str] = []
    skipped: list[str] = []
    for skill in pkg.skills:
        if not skill.tools:
            continue
        for name, tool in skill.tools.items():
            try:
                registry.register(name, tool)
                added.append(name)
            except ValueError:
                skipped.append(name)
    return added, skipped


def unregister_tool_names(registry: Any, names: list[str]) -> list[str]:
    """从主注册表移除工具名；返回实际移除的名称。"""
    removed: list[str] = []
    for name in names:
        if registry.unregister(name):
            removed.append(name)
    return removed


async def discover_packages(
    *,
    skills_root: str | None = None,
    package_dir: str | None = None,
) -> list[SkillPackage]:
    """发现待加载的技能包列表。"""
    if package_dir:
        pkg = await load_skill_package(package_dir)
        return [pkg] if pkg else []
    root = skills_root if skills_root is not None else get_skills_root()
    if not os.path.isdir(root):
        return []
    return await discover_skill_packages(root)


async def load_packages_into_registries(
    registry: Any,
    skill_registry: Any,
    packages: list[SkillPackage],
    *,
    replace: bool = False,
) -> tuple[list[Skill], list[str], list[str]]:
    """将技能包注册到 skill_registry 并将其工具并入主 registry。

    Args:
        registry: 主工具注册表
        skill_registry: 技能注册表
        packages: 待注册包
        replace: True 时先 ``clear_packages`` 再注册（全量 refresh）

    Returns:
        (loaded_skills, added_tool_names, removed_tool_names)
    """
    removed_tools: list[str] = []
    if replace:
        _, prev_tools = skill_registry.clear_packages()
        removed_tools = unregister_tool_names(registry, prev_tools)

    loaded_skills: list[Skill] = []
    added_tools: list[str] = []

    for pkg in packages:
        skill_registry.register_package(pkg)
        loaded_skills.extend(pkg.skills)
        pkg_added, _skipped = _register_package_tools(registry, pkg)
        added_tools.extend(pkg_added)

    return loaded_skills, added_tools, removed_tools


async def bootstrap_skill_packages(
    registry: Any,
    skill_registry: Any,
    *,
    skills_root: str | None = None,
) -> tuple[list[Skill], list[str], list[str]]:
    """启动时全量加载技能目录（等价于 replace=True 的 discover + register）。"""
    packages = await discover_packages(skills_root=skills_root)
    return await load_packages_into_registries(
        registry,
        skill_registry,
        packages,
        replace=True,
    )


__all__ = [
    "bootstrap_skill_packages",
    "discover_packages",
    "load_packages_into_registries",
    "unregister_tool_names",
]
