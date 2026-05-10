"""Engine — 子系统初始化

拆分自 unified.py。

职责：
- 加载技能包
- 创建 SessionManager
- 创建默认会话并加锁
- 清理过期关键词索引
"""

from __future__ import annotations

import os
import random
from pathlib import Path
from typing import Any

from miniagent.infrastructure.logger import get_logger

_logger = get_logger(__name__)


async def init_subsystems(
    registry: Any,
    skill_registry: Any,
    engine: Any,
    SessionManager: Any,
    channel_router: Any,
    clawhub: Any | None = None,
    keyword_index: Any | None = None,
) -> tuple[list, list, list, str, Any]:
    """初始化所有共享子系统。

    Args:
        registry: 工具注册表
        skill_registry: 技能注册表
        engine: UnifiedEngine 实例
        SessionManager: SessionManager 类
        channel_router: 本进程的 :class:`~miniagent.infrastructure.channel_router.ChannelRouter` 实例
        clawhub: ClawHub 客户端，传入 :class:`~miniagent.session.manager.DefaultSessionManager`
        keyword_index: 关键词索引实例（清理过期条目）；未提供则新建默认实例

    Returns:
        (loaded_skills, skill_toolboxes, skill_prompts, active_session_id, session_manager)
    """
    from miniagent.skills.loader import discover_skill_packages
    from miniagent.tools.session_memory import session_memory_tools

    # 1. 加载技能包
    skills_root = os.environ.get(
        "MINI_AGENT_SKILLS",
        str(Path(__file__).parent.parent.parent / "workspaces" / "skills"),
    )
    loaded_skills = []
    if os.path.isdir(skills_root):
        packages = await discover_skill_packages(skills_root)
        for pkg in packages:
            skill_registry.register_package(pkg)
            loaded_skills.extend(pkg.skills)
            for skill in pkg.skills:
                if skill.tools:
                    for name, tool in skill.tools.items():
                        try:
                            registry.register(name, tool)
                        except ValueError:
                            pass

    for name, tool in session_memory_tools.items():
        try:
            registry.register(name, tool)
        except ValueError:
            pass

    # 2. 获取工具箱和系统提示
    skill_toolboxes = skill_registry.get_all_toolboxes()
    skill_prompts = skill_registry.get_system_prompts()

    # 3. 创建 SessionManager
    session_manager = SessionManager(registry, skill_toolboxes, loaded_skills, clawhub=clawhub)

    # 4. 创建默认会话并加锁
    active_session_id = _init_default_session(session_manager, channel_router)

    # 5. 清理过期关键词索引
    try:
        from miniagent.memory.keyword_index import KeywordIndex

        ki = keyword_index if keyword_index is not None else KeywordIndex()
        ki.load()
        ki.prune_expired(30)
    except Exception:
        pass

    return loaded_skills, skill_toolboxes, skill_prompts, active_session_id, session_manager


def _init_default_session(session_manager: Any, channel_router: Any) -> str:
    """创建默认会话并加锁。

    同时将 CLI 通道绑定到 default 会话，确保 CLI 和初始化使用同一会话。

    Returns:
        active_session_id
    """
    from miniagent.engine.session_lock import try_lock_session
    from miniagent.session.manager import SessionOptions

    # 使用统一命名：每个实例的第一个会话都叫 default
    session_id = "default"
    session_manager.get_or_create(session_id, SessionOptions(description="默认会话"))

    # 将 CLI 通道绑定到 default 会话，使 CLI 启动时共享同一会话和历史
    channel_router.bind("__cli__", session_id)
    channel_router.set_primary(session_id)

    # 尝试加锁
    ok, reason = try_lock_session(session_id)
    if ok:
        return session_id

    # 被其他实例占用，创建新会话
    session_id = f"default-{random.randint(1000, 9999)}"
    session_manager.get_or_create(session_id, SessionOptions(description="默认会话"))
    channel_router.bind("__cli__", session_id)
    channel_router.set_primary(session_id)
    try_lock_session(session_id)
    return session_id


__all__ = ["init_subsystems"]
