"""技能注册表 — 技能包与工具箱的进程内索引。

管理所有技能的生命周期：注册、注销、查询、合并、gating。

Gating 机制：
- metadata.bins: 系统必须存在的二进制文件
- metadata.com: 必须可创建的 Windows COM ProgID
- metadata.env: 必须存在的环境变量（可用 SkillEntry.env / api_key+primary_env 满足）
- metadata.config: 必须为真的配置键（AgentConfig 或 SkillEntry.config）
- metadata.os: 适用的操作系统
- metadata.always: 始终可用（跳过所有 gate）
- metadata.disable_model_invocation: 不向模型暴露工具/工具箱/提示词
- metadata.user_invocable: 用户侧调用过滤
- metadata.skill_key: SkillEntry 的备用查找键
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from typing import Any

from miniagent.types.config import AgentConfig
from miniagent.types.skill import (
    Skill,
    SkillEntry,
    SkillMetadata,
    SkillPackage,
    SkillRegistryProtocol,
)
from miniagent.types.tool import Toolbox, ToolDefinition

_logger = logging.getLogger(__name__)


def _lookup_dotted_path(root: Any, path: str) -> Any:
    """按点分路径从对象或 dict 取值（如 ``secrets.tavily_api_key``）。"""
    current: Any = root
    for part in path.split("."):
        if not part:
            continue
        if isinstance(current, dict):
            current = current.get(part)
        elif hasattr(current, part):
            current = getattr(current, part)
        else:
            return None
    return current


def _resolve_api_key(
    api_key: str | dict[str, str] | None,
    config: AgentConfig | None,
) -> str | None:
    """解析 SkillEntry.api_key 为可用字符串。"""
    if api_key is None:
        return None
    if isinstance(api_key, str):
        value = api_key.strip()
        return value or None
    if isinstance(api_key, dict):
        if env_name := api_key.get("env"):
            return os.environ.get(str(env_name)) or None
        source = api_key.get("source") or api_key.get("config")
        if source:
            if config is not None:
                val = _lookup_dotted_path(config, str(source))
                if val:
                    return str(val)
            try:
                from miniagent.infrastructure.json_config import get_config

                val = get_config(str(source))
                if val:
                    return str(val)
            except Exception:
                pass
    return None


def _env_satisfied(
    key: str,
    entry: SkillEntry | None,
    meta: SkillMetadata | None,
    config: AgentConfig | None,
) -> bool:
    """检查单个环境变量 gate 是否满足。"""
    if os.environ.get(key):
        return True
    if entry and key in entry.env and entry.env[key]:
        return True
    if meta and meta.primary_env == key:
        if _resolve_api_key(entry.api_key if entry else None, config):
            return True
    return False


def _config_satisfied(
    key: str,
    entry: SkillEntry | None,
    config: AgentConfig | None,
) -> bool:
    """检查单个配置键 gate 是否满足。"""
    if entry and entry.config.get(key):
        return True
    if config is None:
        return False
    config_dict = config.__dict__ if hasattr(config, "__dict__") else {}
    if config_dict.get(key):
        return True
    return bool(_lookup_dotted_path(config, key))


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
        try:
            getattr(app, "Quit", lambda: None)()
        except Exception as e:
            _logger.debug("COM应用退出失败: %s", e)
        return True
    except Exception:
        return False


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

    def _packaged_skill_ids(self) -> set[str]:
        """属于某技能包的技能 ID 集合。"""
        ids: set[str] = set()
        for pkg in self._packages:
            ids.update(s.id for s in pkg.skills)
        return ids

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

    # ── 配置覆盖 ──

    def set_skill_entries(self, entries: dict[str, SkillEntry]) -> None:
        """设置技能配置覆盖（供外部配置注入使用）。"""
        self._skill_entries = entries

    def get_skill_entry(self, skill_id: str) -> SkillEntry | None:
        """获取指定技能的配置覆盖（支持 ``metadata.skill_key`` 备用键）。"""
        entry = self._skill_entries.get(skill_id)
        if entry is not None:
            return entry
        skill = self.get(skill_id)
        if skill and skill.metadata and skill.metadata.skill_key:
            return self._skill_entries.get(skill.metadata.skill_key)
        return None

    # ── Gating ──

    def _passes_gating(self, skill: Skill, config: AgentConfig | None) -> bool:
        """检查技能是否通过 metadata / entry gating（不含 scope / 模型过滤）。"""
        entry = self.get_skill_entry(skill.id)

        if entry and entry.enabled is False:
            return False

        meta = skill.metadata
        if not meta:
            return True

        if meta.always:
            return True

        if meta.os and sys.platform not in meta.os:
            return False

        if meta.bins and not all(_is_bin_available(b) for b in meta.bins):
            return False

        if meta.com and not all(_is_com_available(c) for c in meta.com):
            return False

        if meta.env and not all(_env_satisfied(k, entry, meta, config) for k in meta.env):
            return False

        if meta.config and not all(_config_satisfied(k, entry, config) for k in meta.config):
            return False

        return True

    def _passes_invocation_filters(
        self,
        skill: Skill,
        *,
        for_model: bool,
        user_invocable_only: bool,
    ) -> bool:
        """检查模型/用户调用过滤。"""
        meta = skill.metadata
        if for_model and meta and meta.disable_model_invocation:
            return False
        if user_invocable_only and meta and not meta.user_invocable:
            return False
        return True

    def get_eligible_skills(
        self,
        config: AgentConfig | None = None,
        *,
        session_key: str | None = None,
        for_model: bool = False,
        user_invocable_only: bool = False,
    ) -> list[Skill]:
        """根据 gating 条件过滤可用的技能。"""
        eligible: list[Skill] = []
        for skill in self._get_matching_skills(
            config,
            session_key,
            for_model=for_model,
            user_invocable_only=user_invocable_only,
            require_gating=True,
        ):
            eligible.append(skill)
        return eligible

    # ── 聚合查询 ──

    def _get_matching_scopes(self, session_key: str | None = None) -> set[str]:
        """根据 session_key 解析需要查询的 scope 集合。"""
        if session_key is None:
            return set(self._scope_index.keys())
        session_id = session_key
        if session_key.startswith("feishu:"):
            session_id = session_key[len("feishu:"):]
        elif session_key.startswith("cli:"):
            session_id = session_key[len("cli:"):]
        return {"global", f"session:{session_id}"}

    def _skill_in_allowed_scope(self, skill_id: str, session_key: str | None) -> bool:
        """技能是否落在允许的 scope 内（非包内注册的技能视为 global）。"""
        packaged = self._packaged_skill_ids()
        if skill_id not in packaged:
            return True
        scopes = self._get_matching_scopes(session_key)
        for scope in scopes:
            for pkg_id in self._scope_index.get(scope, []):
                pkg = self.get_package(pkg_id)
                if pkg and any(s.id == skill_id for s in pkg.skills):
                    return True
        return False

    def _get_matching_skills(
        self,
        config: AgentConfig | None = None,
        session_key: str | None = None,
        *,
        for_model: bool = True,
        user_invocable_only: bool = False,
        require_gating: bool = True,
    ) -> list[Skill]:
        """按 scope + gating + 调用过滤返回技能列表。"""
        result: list[Skill] = []
        for skill in self._skills.values():
            if not self._skill_in_allowed_scope(skill.id, session_key):
                continue
            if require_gating and not self._passes_gating(skill, config):
                continue
            if not self._passes_invocation_filters(
                skill,
                for_model=for_model,
                user_invocable_only=user_invocable_only,
            ):
                continue
            result.append(skill)
        return result

    def get_all_toolboxes(
        self,
        config: AgentConfig | None = None,
        *,
        session_key: str | None = None,
        for_model: bool = True,
    ) -> list[Toolbox]:
        """获取可用技能贡献的工具箱（经 gating + scope 过滤，自动去重）。"""
        seen: set[str] = set()
        result: list[Toolbox] = []
        for skill in self._get_matching_skills(
            config, session_key, for_model=for_model,
        ):
            if not skill.toolboxes:
                continue
            for tb in skill.toolboxes:
                if tb.id not in seen:
                    seen.add(tb.id)
                    result.append(tb)
        return result

    def get_all_tools(
        self,
        config: AgentConfig | None = None,
        *,
        session_key: str | None = None,
        for_model: bool = True,
    ) -> dict[str, ToolDefinition]:
        """获取可用技能贡献的工具定义（经 gating + scope 过滤）。"""
        result: dict[str, ToolDefinition] = {}
        for skill in self._get_matching_skills(
            config, session_key, for_model=for_model,
        ):
            if skill.tools:
                result.update(skill.tools)
        return result

    def get_system_prompts(
        self,
        config: AgentConfig | None = None,
        *,
        session_key: str | None = None,
        for_model: bool = True,
    ) -> list[str]:
        """获取可用技能的系统提示词增强（经 gating + scope 过滤）。"""
        prompts: list[str] = []
        for skill in self._get_matching_skills(
            config, session_key, for_model=for_model,
        ):
            if skill.system_prompt and skill.system_prompt.strip():
                prompts.append(skill.system_prompt)
        return prompts


__all__ = ["DefaultSkillRegistry"]
