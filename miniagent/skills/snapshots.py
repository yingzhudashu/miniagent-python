"""技能工具箱 / 系统提示词快照 — 与 CLI state 同步。"""

from __future__ import annotations

from typing import Any

from miniagent.skills.builtin_toolboxes import BUILTIN_TOOLBOXES
from miniagent.types.config import AgentConfig
from miniagent.types.tool import Toolbox


def build_skill_snapshots(
    skill_registry: Any,
    config: AgentConfig | None = None,
    *,
    session_key: str | None = None,
) -> tuple[list[Toolbox], list[str]]:
    """从技能注册表构建工具箱列表与系统提示词列表（含内置工具箱）。

    ``session_key=None`` 时返回所有 scope（向后兼容）；
    ``session_key`` 非 None 时仅返回 global + 该会话 scope 的技能。
    """
    skill_toolboxes = skill_registry.get_all_toolboxes(config, session_key=session_key)
    seen_tb = {t.id for t in skill_toolboxes}
    for tb in BUILTIN_TOOLBOXES:
        if tb.id not in seen_tb:
            skill_toolboxes.append(tb)
            seen_tb.add(tb.id)
    skill_prompts = skill_registry.get_system_prompts(config, session_key=session_key)
    return skill_toolboxes, skill_prompts


def get_skill_toolboxes_from_state(state: dict[str, Any] | None) -> list[Any]:
    """从 CLI 运行时 state 读取技能工具箱快照。"""
    if not state:
        return []
    return list(state.get("skill_toolboxes") or [])


def get_skill_prompts_from_state(state: dict[str, Any] | None) -> list[str]:
    """从 CLI 运行时 state 读取技能系统提示词快照。"""
    if not state:
        return []
    return list(state.get("skill_prompts") or [])


def join_skill_prompts(prompts: list[str] | None) -> str | None:
    """将提示词列表合并为单段 system augment。"""
    if not prompts:
        return None
    joined = "\n\n".join(p for p in prompts if p and str(p).strip())
    return joined or None


def apply_skill_snapshots_to_state(
    state: dict[str, Any],
    *,
    skill_toolboxes: list[Any],
    skill_prompts: list[str],
    loaded_skills: list[Any] | None = None,
    session_manager: Any | None = None,
) -> None:
    """将 refresh 结果写回共享 state（及可选 SessionManager）。"""
    state["skill_toolboxes"] = skill_toolboxes
    state["skill_prompts"] = skill_prompts
    sm = session_manager if session_manager is not None else state.get("session_manager")
    if sm is not None and loaded_skills is not None and hasattr(sm, "refresh_main_skills"):
        sm.refresh_main_skills(loaded_skills, skill_toolboxes)


__all__ = [
    "apply_skill_snapshots_to_state",
    "build_skill_snapshots",
    "get_skill_prompts_from_state",
    "get_skill_toolboxes_from_state",
    "join_skill_prompts",
]
