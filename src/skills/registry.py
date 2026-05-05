"""Mini Agent Python — 技能注册表 (Phase 6)

管理所有技能的生命周期：注册、注销、查询、合并、gating。

Gating 机制（参考 OpenClaw metadata）：
- requires.bins: 系统必须存在的二进制文件
- requires.env: 必须存在的环境变量
- requires.config: 必须为真的 AgentConfig 键
- os: 适用的操作系统
- always: 始终可用（跳过所有 gate）
"""

from __future__ import annotations

import os
import shutil
import sys
from typing import Any

from src.types.skill import (
    Skill,
    SkillPackage,
    SkillEntry,
    SkillRegistryProtocol,
)
from src.types.tool import Toolbox, ToolDefinition
from src.types.config import AgentConfig


class DefaultSkillRegistry(SkillRegistryProtocol):
    """默认技能注册表实现。

    使用 dict 存储，O(1) 查询。后注册的同名技能覆盖先注册的。
    """

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}
        self._packages: list[SkillPackage] = []
        self._skill_entries: dict[str, SkillEntry] = {}

    # ── 基本操作 ──

    def register(self, skill: Skill) -> None:
        """注册一个技能。同名覆盖。"""
        self._skills[skill.id] = skill

    def unregister(self, skill_id: str) -> bool:
        """注销一个技能。"""
        if skill_id in self._skills:
            del self._skills[skill_id]
            return True
        return False

    def get(self, skill_id: str) -> Skill | None:
        """获取指定技能。"""
        return self._skills.get(skill_id)

    def get_all(self) -> list[Skill]:
        """获取所有已注册技能。"""
        return list(self._skills.values())

    # ── 技能包 ──

    def register_package(self, pkg: SkillPackage) -> None:
        """注册技能包（批量注册其中所有技能）。"""
        self._packages.append(pkg)
        for skill in pkg.skills:
            if not skill.skill_md and pkg.skill_md:
                skill.skill_md = pkg.skill_md
            self.register(skill)

    def get_packages(self) -> list[SkillPackage]:
        """获取所有已注册的技能包。"""
        return list(self._packages)

    # ── 聚合查询 ──

    def get_all_toolboxes(self) -> list[Toolbox]:
        """获取所有技能贡献的工具箱（自动去重）。"""
        seen: set[str] = set()
        result: list[Toolbox] = []
        for skill in self._skills.values():
            if not skill.toolboxes:
                continue
            for tb in skill.toolboxes:
                if tb.id not in seen:
                    seen.add(tb.id)
                    result.append(tb)
        return result

    def get_all_tools(self) -> dict[str, ToolDefinition]:
        """获取所有技能贡献的工具定义。后注册覆盖先注册。"""
        result: dict[str, ToolDefinition] = {}
        for skill in self._skills.values():
            if skill.tools:
                result.update(skill.tools)
        return result

    def get_system_prompts(self) -> list[str]:
        """获取所有技能的系统提示词增强。"""
        prompts: list[str] = []
        for skill in self._skills.values():
            if skill.system_prompt and skill.system_prompt.strip():
                prompts.append(skill.system_prompt)
        return prompts

    # ── 配置覆盖 ──

    def set_skill_entries(self, entries: dict[str, SkillEntry]) -> None:
        """设置技能配置覆盖。"""
        self._skill_entries = entries

    def get_skill_entry(self, skill_id: str) -> SkillEntry | None:
        """获取指定技能的配置覆盖。"""
        return self._skill_entries.get(skill_id)

    # ── Gating ──

    def get_eligible_skills(self, config: AgentConfig | None = None) -> list[Skill]:
        """根据 gating 条件过滤可用的技能。"""
        eligible: list[Skill] = []

        for skill in self._skills.values():
            entry = self._skill_entries.get(skill.id)

            # enabled=false → 禁用
            if entry and entry.enabled is False:
                continue

            meta = skill.metadata
            if not meta:
                eligible.append(skill)
                continue

            # always=true → 跳过所有 gate
            if meta.always:
                eligible.append(skill)
                continue

            # 操作系统检查
            if meta.os and len(meta.os) > 0:
                current_os = sys.platform
                if current_os not in meta.os:
                    continue

            # 二进制文件检查
            if meta.bins and len(meta.bins) > 0:
                if not all(_is_bin_available(b) for b in meta.bins):
                    continue

            # 环境变量检查
            if meta.env and len(meta.env) > 0:
                env_map = entry.env if entry and entry.env else {}
                if not all(os.environ.get(k) or k in env_map for k in meta.env):
                    continue

            # config 键检查
            if meta.config and len(meta.config) > 0 and config:
                config_dict = config.__dict__ if hasattr(config, "__dict__") else {}
                if not all(config_dict.get(k) for k in meta.config):
                    continue

            eligible.append(skill)

        return eligible


def _is_bin_available(bin_name: str) -> bool:
    """检查二进制文件是否在 PATH 上可用。"""
    return shutil.which(bin_name) is not None


__all__ = ["DefaultSkillRegistry"]
