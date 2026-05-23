"""Engine — 子系统初始化

拆分自 unified.py。在 ``unified_main`` 早期调用 ``init_subsystems``，完成 **工具与技能**
的就绪后再进入 CLI 循环。

顺序要点（与注册覆盖语义一致）：

1. ``register_builtin_tools``：内置 ``ALL_TOOLS``
2. 磁盘技能包发现并注册工具（同名冲突时跳过技能侧）
3. ``session_memory_tools``：会话级记忆工具
4. 可选 ``MINIAGENT_MCP_STDIO``：stdio MCP 工具（未安装 ``mcp`` 包则打日志跳过）
5. 合并 ``BUILTIN_TOOLBOXES`` 与技能工具箱；创建 ``SessionManager``；默认会话加锁；
   ``KeywordIndex.prune_expired`` 清理过期索引项

环境与可选组件开关汇总见根目录 ``README``、``.env.example``；架构见 ``docs/ARCHITECTURE.md``。
"""

from __future__ import annotations

import json
import os
import random
import shutil
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
    from miniagent.engine.builtin_tools import register_builtin_tools
    from miniagent.skills.load_runtime import bootstrap_skill_packages
    from miniagent.skills.snapshots import build_skill_snapshots
    from miniagent.tools.session_memory import session_memory_tools

    # 0.5. 检查并恢复 baseline skills（skill-vetter / skill-creator）
    _ensure_baseline_skills()

    # 0. 内置工具（ALL_TOOLS）先于技能包；同名时内置优先（技能注册遇 ValueError 则跳过）
    reg_n = register_builtin_tools(registry)
    if reg_n:
        _logger.info("已注册 %d 个内置工具（ALL_TOOLS）", reg_n)

    loaded_skills, _added, _removed = await bootstrap_skill_packages(registry, skill_registry)

    for name, tool in session_memory_tools.items():
        try:
            registry.register(name, tool)
        except ValueError:
            pass

    mcp_raw = os.environ.get("MINIAGENT_MCP_STDIO", "").strip()
    if mcp_raw:
        try:
            spec = json.loads(mcp_raw)
            if isinstance(spec, list) and len(spec) >= 1:
                from miniagent.mcp.runtime import register_mcp_stdio_tools

                mcp_n = await register_mcp_stdio_tools(
                    registry, str(spec[0]), [str(x) for x in spec[1:]]
                )
                _logger.info("MINIAGENT_MCP_STDIO: 已注册 %d 个 MCP 工具", mcp_n)
        except ImportError:
            _logger.warning(
                "MINIAGENT_MCP_STDIO: 未安装 mcp 包，跳过（pip install miniagent-python[mcp]）"
            )
        except Exception as e:
            _logger.warning("MINIAGENT_MCP_STDIO: %s", e)

    # 2. 获取工具箱和系统提示（含 gating）
    from miniagent.core.config import get_default_agent_config

    try:
        agent_cfg = get_default_agent_config()
    except Exception:
        agent_cfg = None
    skill_toolboxes, skill_prompts = build_skill_snapshots(skill_registry, agent_cfg)

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


_BASELINE_SKILLS = ("skill-vetter", "skill-creator", "builtin-web")


def _ensure_baseline_skills() -> None:
    """检查 baseline skills 是否存在，缺失则从模板恢复。

    若技能根目录不存在，会自动创建后再恢复。
    """
    skills_root = _get_skills_root_for_baseline()
    if not skills_root:
        return

    os.makedirs(skills_root, exist_ok=True)

    templates_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills", "templates"
    )
    if not os.path.isdir(templates_dir):
        return

    for name in _BASELINE_SKILLS:
        target = os.path.join(skills_root, name)
        if not os.path.isdir(target):
            src = os.path.join(templates_dir, name)
            if os.path.isdir(src):
                shutil.copytree(src, target)
                _logger.info("已恢复 baseline skill: %s", name)


def _get_skills_root_for_baseline() -> str | None:
    try:
        from miniagent.skills.paths import get_skills_root

        return get_skills_root()
    except Exception:
        return None


__all__ = ["init_subsystems"]
