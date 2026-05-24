"""运行期技能热加载 — 安装或手工变更后无需重启进程。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from miniagent.core.config import get_default_agent_config
from miniagent.infrastructure.logger import get_logger
from miniagent.skills.load_runtime import (
    discover_packages,
    load_packages_into_registries,
    unregister_tool_names,
)
from miniagent.skills.snapshots import apply_skill_snapshots_to_state, build_skill_snapshots
from miniagent.types.config import AgentConfig
from miniagent.types.skill import Skill
from miniagent.types.tool import Toolbox

_logger = get_logger(__name__)


@dataclass
class RefreshResult:
    """``refresh_skills`` 执行结果。"""

    loaded_skills: list[Skill] = field(default_factory=list)
    skill_toolboxes: list[Toolbox] = field(default_factory=list)
    skill_prompts: list[str] = field(default_factory=list)
    added_tools: list[str] = field(default_factory=list)
    removed_tools: list[str] = field(default_factory=list)
    package_ids: list[str] = field(default_factory=list)


def _resolve_agent_config(config: AgentConfig | None) -> AgentConfig | None:
    if config is not None:
        return config
    try:
        return get_default_agent_config()
    except Exception:
        return None


async def refresh_skills(
    registry: Any,
    skill_registry: Any,
    *,
    package_dir: str | None = None,
    skills_root: str | None = None,
    config: AgentConfig | None = None,
    state: dict[str, Any] | None = None,
    session_manager: Any | None = None,
) -> RefreshResult:
    """重新发现磁盘技能并更新注册表与快照。

    Args:
        registry: 主工具注册表
        skill_registry: 技能注册表
        package_dir: 仅刷新指定包目录；为 None 时全量重扫（主根 + 会话技能目录）
        skills_root: 技能根目录（None 时启用多根发现）
        config: Agent 配置（gating）；None 时尝试 ``get_default_agent_config()``
        state: 可选 CLI state，成功时写入 ``skill_toolboxes`` / ``skill_prompts``
        session_manager: 可选，同步主空间技能列表

    Returns:
        RefreshResult
    """
    agent_cfg = _resolve_agent_config(config)
    removed_tools: list[str] = []

    if package_dir:
        pkg_dir = os.path.abspath(package_dir)
        package_id = os.path.basename(pkg_dir.rstrip(os.sep))
        _, prev_tools = skill_registry.unregister_package(package_id)
        removed_tools = unregister_tool_names(registry, prev_tools)
        packages = await discover_packages(package_dir=pkg_dir)
        _, added_tools, _ = await load_packages_into_registries(
            registry,
            skill_registry,
            packages,
            replace=False,
        )
    else:
        # 多根发现：主根优先，随后会话技能目录
        packages = await discover_packages(
            skills_root=skills_root,
            include_sessions=skills_root is None,
        )
        loaded_skills, added_tools, removed_tools = await load_packages_into_registries(
            registry,
            skill_registry,
            packages,
            replace=True,
        )

    package_ids = [p.id for p in packages]
    all_skills = list(skill_registry.get_all())

    # 从 state 中读取 active_session_id，用于 scope 过滤
    session_key: str | None = None
    if state is not None:
        session_key = (state.get("active_session_id") or "").strip() or None

    skill_toolboxes, skill_prompts = build_skill_snapshots(
        skill_registry,
        agent_cfg,
        session_key=session_key,
    )
    result = RefreshResult(
        loaded_skills=all_skills,
        skill_toolboxes=skill_toolboxes,
        skill_prompts=skill_prompts,
        added_tools=added_tools,
        removed_tools=removed_tools,
        package_ids=package_ids,
    )
    _log_refresh(result)
    if state is not None:
        apply_skill_snapshots_to_state(
            state,
            skill_toolboxes=skill_toolboxes,
            skill_prompts=skill_prompts,
            loaded_skills=all_skills,
            session_manager=session_manager,
        )
    return result


def _log_refresh(result: RefreshResult) -> None:
    _logger.info(
        "技能 refresh: 包=%s 技能数=%d 新增工具=%d 移除工具=%d",
        result.package_ids or "(none)",
        len(result.loaded_skills),
        len(result.added_tools),
        len(result.removed_tools),
    )


__all__ = ["RefreshResult", "refresh_skills"]
