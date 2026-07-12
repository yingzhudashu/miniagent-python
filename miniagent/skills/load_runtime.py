"""技能包加载到注册表的共用逻辑（启动与 refresh 共用）。"""

from __future__ import annotations

import os
from typing import Any

from miniagent.infrastructure.logger import get_logger
from miniagent.skills.loader import discover_skill_packages, load_skill_package
from miniagent.skills.paths import get_all_skill_roots, resolve_scope_for_root
from miniagent.types.config import AgentConfig
from miniagent.types.skill import Skill, SkillPackage

_logger = get_logger(__name__)


def _register_package_tools(
    registry: Any,
    pkg: SkillPackage,
    *,
    eligible_skill_ids: set[str] | None = None,
) -> tuple[list[str], list[str]]:
    """将技能包内工具注册到主工具注册表（内置同名则跳过）。

    Args:
        registry: 主工具注册表
        pkg: 技能包
        eligible_skill_ids: 若提供，仅注册 gating 通过的技能工具

    Returns:
        (added_tool_names, skipped_tool_names)
    """
    added: list[str] = []
    skipped: list[str] = []
    for skill in pkg.skills:
        if eligible_skill_ids is not None and skill.id not in eligible_skill_ids:
            continue
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
    include_sessions: bool = True,
) -> list[SkillPackage]:
    """发现待加载的技能包列表。

    若未指定 ``skills_root`` 或 ``package_dir``，则扫描所有可用技能根目录
    （主根 + 会话技能目录）。
    """
    if package_dir:
        pkg = await load_skill_package(package_dir)
        if pkg:
            pkg.scope = resolve_scope_for_root(os.path.dirname(package_dir))
            return [pkg]
        return []
    if skills_root:
        if not os.path.isdir(skills_root):
            return []
        scope = resolve_scope_for_root(skills_root)
        root_packages = await discover_skill_packages(skills_root)
        for pkg in root_packages:
            pkg.scope = scope
        return root_packages
    # 多根发现：主根优先，随后按会话顺序扫描
    all_roots = get_all_skill_roots(include_sessions=include_sessions)
    seen_ids: set[str] = set()
    packages: list[SkillPackage] = []
    for root in all_roots:
        if not os.path.isdir(root):
            continue
        scope = resolve_scope_for_root(root)
        for pkg in await discover_skill_packages(root):
            if pkg.id not in seen_ids:
                pkg.scope = scope
                seen_ids.add(pkg.id)
                packages.append(pkg)
            else:
                _logger.debug("技能包 %s 已在主根注册，跳过会话根 %s", pkg.id, root)
    return packages


async def load_packages_into_registries(
    registry: Any,
    skill_registry: Any,
    packages: list[SkillPackage],
    *,
    replace: bool = False,
    config: AgentConfig | None = None,
) -> tuple[list[Skill], list[str], list[str]]:
    """将技能包注册到 skill_registry 并将其工具并入主 registry。

    Args:
        registry: 主工具注册表
        skill_registry: 技能注册表
        packages: 待注册包
        replace: True 时先 ``clear_packages`` 再注册（全量 refresh）
        config: Agent 配置（gating）；None 时不做 env/config gate

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

    eligible_ids = {
        s.id for s in skill_registry.get_eligible_skills(config, for_model=True)
    }

    for pkg in packages:
        pkg_added, _skipped = _register_package_tools(
            registry, pkg, eligible_skill_ids=eligible_ids,
        )
        added_tools.extend(pkg_added)

    return loaded_skills, added_tools, removed_tools


async def bootstrap_skill_packages(
    registry: Any,
    skill_registry: Any,
    *,
    skills_root: str | None = None,
    config: AgentConfig | None = None,
) -> tuple[list[Skill], list[str], list[str]]:
    """启动时全量加载技能目录（等价于 replace=True 的 discover + register）。

    若未指定 ``skills_root``，则自动扫描主根 + 所有会话技能目录。
    """
    if skills_root:
        packages = await discover_packages(skills_root=skills_root)
    else:
        packages = await discover_packages(include_sessions=True)
    return await load_packages_into_registries(
        registry,
        skill_registry,
        packages,
        replace=True,
        config=config,
    )


__all__ = [
    "bootstrap_skill_packages",
    "discover_packages",
    "load_packages_into_registries",
    "unregister_tool_names",
]
