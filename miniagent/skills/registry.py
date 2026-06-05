"""技能注册表 — 技能包与工具箱的进程内索引。

管理所有技能的生命周期：注册、注销、查询、合并、gating。

Gating 机制：
- requires.bins: 系统必须存在的二进制文件
- requires.com: 必须可创建的 Windows COM ProgID
- requires.env: 必须存在的环境变量
- requires.config: 必须为真的 AgentConfig 键
- os: 适用的操作系统
- always: 始终可用（跳过所有 gate）
"""

from __future__ import annotations

import logging
import os
import shutil
import sys

from miniagent.types.config import AgentConfig
from miniagent.types.skill import (
    Skill,
    SkillEntry,
    SkillPackage,
    SkillRegistryProtocol,
)
from miniagent.types.tool import Toolbox, ToolDefinition

_logger = logging.getLogger(__name__)


class DefaultSkillRegistry(SkillRegistryProtocol):
    """默认技能注册表实现。

    使用 dict 存储，O(1) 查询。后注册的同名技能覆盖先注册的。
    """

    def __init__(self) -> None:
        """初始化空注册表与包列表。"""
        self._skills: dict[str, Skill] = {}
        self._packages: list[SkillPackage] = []
        self._skill_entries: dict[str, SkillEntry] = {}
        self._scope_index: dict[str, list[str]] = {}  # scope → [pkg_id, ...]

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

    def _collect_registered_skill_tool_names(self) -> list[str]:
        """汇总已注册技能贡献的工具名（不过 gating，供 refresh 卸载主 registry）。"""
        names: list[str] = []
        for skill in self._skills.values():
            if skill.tools:
                names.extend(skill.tools.keys())
        return names

    # ── 技能包 ──

    def register_package(self, pkg: SkillPackage) -> None:
        """注册技能包（批量注册其中所有技能）。"""
        self._packages.append(pkg)
        scope = pkg.scope or "global"
        self._scope_index.setdefault(scope, []).append(pkg.id)
        for skill in pkg.skills:
            if not skill.skill_md and pkg.skill_md:
                skill.skill_md = pkg.skill_md
            self.register(skill)

    def get_packages(self) -> list[SkillPackage]:
        """获取所有已注册的技能包。"""
        return list(self._packages)

    def get_package(self, package_id: str) -> SkillPackage | None:
        """按包 ID 查询已注册的技能包。"""
        for pkg in self._packages:
            if pkg.id == package_id:
                return pkg
        return None

    def unregister_package(self, package_id: str) -> tuple[list[str], list[str]]:
        """注销指定技能包及其下属技能。

        Returns:
            (removed_skill_ids, removed_tool_names)
        """
        removed_skill_ids: list[str] = []
        removed_tool_names: list[str] = []
        kept: list[SkillPackage] = []
        for pkg in self._packages:
            if pkg.id != package_id:
                kept.append(pkg)
                continue
            scope = pkg.scope or "global"
            if scope in self._scope_index and package_id in self._scope_index[scope]:
                self._scope_index[scope].remove(package_id)
                if not self._scope_index[scope]:
                    del self._scope_index[scope]
            for skill in pkg.skills:
                removed_skill_ids.append(skill.id)
                if skill.tools:
                    removed_tool_names.extend(skill.tools.keys())
                self.unregister(skill.id)
        self._packages = kept
        return removed_skill_ids, removed_tool_names

    def clear_packages(self) -> tuple[list[str], list[str]]:
        """清空所有技能包与技能索引（不触及工具注册表）。

        Returns:
            (removed_skill_ids, removed_tool_names)
        """
        removed_skill_ids = list(self._skills.keys())
        removed_tool_names = self._collect_registered_skill_tool_names()
        self._skills.clear()
        self._packages.clear()
        self._scope_index.clear()
        return removed_skill_ids, removed_tool_names

    # ── 聚合查询 ──

    def _get_matching_scopes(self, session_key: str | None = None) -> set[str]:
        """根据 session_key 解析需要查询的 scope 集合。

        ``session_key=None`` → 返回所有 scope；
        ``session_key="feishu:xxx"`` → 返回 ``{"global", "session:xxx"}``。
        """
        if session_key is None:
            return set(self._scope_index.keys())
        # 尝试从 session_key 中提取会话 ID
        session_id = session_key
        if session_key.startswith("feishu:"):
            session_id = session_key[len("feishu:"):]
        elif session_key.startswith("cli:"):
            session_id = session_key[len("cli:"):]
        return {"global", f"session:{session_id}"}

    def _get_matching_packages(self, session_key: str | None = None) -> list[SkillPackage]:
        """按 scope 过滤技能包。

        返回全局包 + 指定会话的包（若 ``session_key`` 非 None）。
        """
        scopes = self._get_matching_scopes(session_key)
        pkg_ids: set[str] = set()
        for scope in scopes:
            pkg_ids.update(self._scope_index.get(scope, []))
        # 保持原始注册顺序
        return [pkg for pkg in self._packages if pkg.id in pkg_ids]

    def _get_matching_skills(self, config: AgentConfig | None = None, session_key: str | None = None) -> list[Skill]:
        """按 scope 过滤后执行 gating，返回可用技能列表。"""
        scopes = self._get_matching_scopes(session_key)
        # 用 _scope_index 建立 skill_id → True 集合（O(1) 查询）
        allowed_skill_ids: set[str] = set()
        for scope in scopes:
            for pkg_id in self._scope_index.get(scope, []):
                pkg = self.get_package(pkg_id)
                if pkg:
                    allowed_skill_ids.update(s.id for s in pkg.skills)

        eligible: list[Skill] = []
        for skill in self.get_eligible_skills(config):
            if skill.id in allowed_skill_ids:
                eligible.append(skill)
        return eligible

    def get_all_toolboxes(
        self,
        config: AgentConfig | None = None,
        *,
        session_key: str | None = None,
    ) -> list[Toolbox]:
        """获取可用技能贡献的工具箱（经 gating + scope 过滤，自动去重）。

        ``session_key=None`` 时返回所有 scope 的工具箱；
        ``session_key`` 非 None 时仅返回 global + 该会话 scope 的工具箱。
        """
        seen: set[str] = set()
        result: list[Toolbox] = []
        for skill in self._get_matching_skills(config, session_key):
            if not skill.toolboxes:
                continue
            for tb in skill.toolboxes:
                if tb.id not in seen:
                    seen.add(tb.id)
                    result.append(tb)
        return result

    def get_all_tools(self, config: AgentConfig | None = None) -> dict[str, ToolDefinition]:
        """获取可用技能贡献的工具定义（经 gating 过滤）。"""
        result: dict[str, ToolDefinition] = {}
        for skill in self.get_eligible_skills(config):
            if skill.tools:
                result.update(skill.tools)
        return result

    def get_system_prompts(
        self,
        config: AgentConfig | None = None,
        *,
        session_key: str | None = None,
    ) -> list[str]:
        """获取可用技能的系统提示词增强（经 gating + scope 过滤）。

        ``session_key=None`` 时返回所有 scope 的提示词；
        ``session_key`` 非 None 时仅返回 global + 该会话 scope 的提示词。
        """
        prompts: list[str] = []
        for skill in self._get_matching_skills(config, session_key):
            if skill.system_prompt and skill.system_prompt.strip():
                prompts.append(skill.system_prompt)
        return prompts

    # ── 配置覆盖（协议要求，供外部配置注入使用）──

    def set_skill_entries(self, entries: dict[str, SkillEntry]) -> None:
        """设置技能配置覆盖（供外部配置注入使用）。"""
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

            # Windows COM 对象检查
            if meta.com and len(meta.com) > 0:
                if not all(_is_com_available(c) for c in meta.com):
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


def _is_com_available(progid: str) -> bool:
    """检查 Windows COM ProgID 是否可创建。

    非 Windows 平台始终返回 ``False``。
    """
    if os.name != "nt":
        return False
    try:
        import win32com.client

        app = win32com.client.Dispatch(progid)
        # 尝试安全退出（部分 COM 对象不支持 Quit）
        try:
            getattr(app, "Quit", lambda: None)()
        except Exception as e:
            _logger.debug("COM应用退出失败: %s", e)
        return True
    except Exception:
        return False


__all__ = ["DefaultSkillRegistry"]
