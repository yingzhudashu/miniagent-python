"""Engine — 子系统初始化

拆分自 unified.py。在 ``unified_main`` 早期调用 ``init_subsystems``，完成 **工具与技能**
的就绪后再进入 CLI 循环。

顺序要点（与注册覆盖语义一致）：

1. ``register_builtin_tools``：内置 ``ALL_TOOLS``（含 session_memory_tools）
2. 磁盘技能包发现并注册工具（同名冲突时跳过技能侧）
3. 可选 ``mcp.stdio_command``：stdio MCP 工具（未安装 ``mcp`` 包则打日志跳过）
4. ``build_skill_snapshots``（内含 ``BUILTIN_TOOLBOXES`` 合并）；创建 ``SessionManager``；默认会话加锁；
   ``KeywordIndex.prune_expired`` 清理过期索引项

配置见 config.defaults.json；架构见 ``docs/ARCHITECTURE.md``。
"""

from __future__ import annotations

import json
import os
import random
import shutil
from typing import Any

from miniagent.infrastructure.env_parse import env_flag, env_str
from miniagent.infrastructure.json_config import get_config
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
        engine: 保留以兼容旧调用方，当前未使用
        SessionManager: SessionManager 类
        channel_router: 本进程的 :class:`~miniagent.infrastructure.channel_router.ChannelRouter` 实例
        clawhub: ClawHub 客户端，传入 :class:`~miniagent.session.manager.DefaultSessionManager`
        keyword_index: 关键词索引实例（清理过期条目）；未提供则新建默认实例

    Returns:
        (loaded_skills, skill_toolboxes, skill_prompts, active_session_id, session_manager)
    """
    from miniagent.engine.builtin_tools import register_builtin_tools

    # 0. 自动注册 trace 持久化钩子（如果设置了 MINIAGENT_TRACE_LOG_FILE）
    from miniagent.infrastructure.tracing import auto_register_trace_file_hook
    from miniagent.skills.load_runtime import bootstrap_skill_packages
    from miniagent.skills.snapshots import build_skill_snapshots

    _ = engine  # API 兼容占位
    auto_register_trace_file_hook()

    # 0.5. 检查并恢复 baseline skills（skill-vetter / skill-creator）
    _ensure_baseline_skills()

    # 1. 内置工具（ALL_TOOLS）先于技能包；同名时内置优先（技能注册遇 ValueError 则跳过）
    # session_memory_tools 已在 ALL_TOOLS 中统一注册
    reg_n = register_builtin_tools(registry)
    if reg_n:
        _logger.info("已注册 %d 个内置工具（ALL_TOOLS）", reg_n)

    loaded_skills, _added, _removed = await bootstrap_skill_packages(registry, skill_registry)

    await _register_mcp_tools_from_config(registry)

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
    session_name = env_str("MINIAGENT_SESSION_NAME") or get_config("session.default_name", None)
    active_session_id = _init_default_session(session_manager, channel_router, session_name=session_name)

    # 5. 清理过期关键词索引
    try:
        from miniagent.memory.keyword_index import KeywordIndex

        ki = keyword_index if keyword_index is not None else KeywordIndex()
        ki.load()
        ki.prune_expired()
    except Exception as e:
        _logger.debug("关键词索引初始化失败: %s", e)

    return loaded_skills, skill_toolboxes, skill_prompts, active_session_id, session_manager


def _resolve_continue_session_id(session_manager: Any, channel_router: Any) -> str:
    """在 ``--continue`` 模式下解析应恢复的会话 ID。"""
    from miniagent.session.manager import session_info_id

    existing_sessions = session_manager.list_all_sessions_with_info()
    existing_ids = {session_info_id(s) for s in existing_sessions if session_info_id(s)}

    last_state = channel_router.load_cli_session_state()
    if last_state:
        last_session_id = last_state.get("last_cli_session")
        if last_session_id:
            if last_session_id in existing_ids:
                return last_session_id
            _logger.info("上次会话 %s 已删除，尝试其它回退", last_session_id)

    primary = channel_router.primary
    if primary and primary in existing_ids:
        return primary

    cli_bound = channel_router.resolve("__cli__")
    if cli_bound != "__cli__" and cli_bound in existing_ids:
        return cli_bound

    return "default"


def _init_default_session(session_manager: Any, channel_router: Any, *, session_name: str | None = None) -> str:
    """创建默认会话并加锁。

    同时将 CLI 通道绑定到默认会话，确保 CLI 和初始化使用同一会话。

    Args:
        session_manager: 会话管理器实例
        channel_router: 通道路由器，用于加载上次会话状态（--continue 功能）和绑定 CLI 通道
        session_name: 可选的会话名称（由 ``MINIAGENT_SESSION_NAME`` 传入）。
            若不传，则使用 ``"default"`` 或上次会话（--continue 模式）。

    Returns:
        active_session_id
    """
    from miniagent.engine.session_lock import try_lock_session
    from miniagent.session.manager import SessionOptions

    # --continue 参数支持：优先恢复上次会话
    continue_mode = get_config("session.continue_mode", False) or env_flag(
        "MINIAGENT_CONTINUE_SESSION"
    )

    if continue_mode and not session_name:
        session_id = _resolve_continue_session_id(session_manager, channel_router)
    elif session_name:
        session_id = session_name
    else:
        session_id = "default"

    session_manager.get_or_create(session_id, SessionOptions(description="默认会话"))
    channel_router.bind("__cli__", session_id)
    channel_router.set_primary(session_id)

    ok, reason = try_lock_session(session_id)
    if ok:
        return session_id

    # 被其他实例占用，自动回退
    fallback = f"{session_id}-{random.randint(1000, 9999)}"
    _logger.info(
        "会话 %s 加锁失败%s，回退到 %s",
        session_id,
        f" ({reason})" if reason else "",
        fallback,
    )
    session_manager.get_or_create(fallback, SessionOptions(description="默认会话（回退）"))
    channel_router.bind("__cli__", fallback)
    channel_router.set_primary(fallback)
    fb_ok, fb_reason = try_lock_session(fallback)
    if not fb_ok:
        _logger.warning("回退会话 %s 加锁失败: %s", fallback, fb_reason)
    return fallback


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
                try:
                    shutil.copytree(src, target)
                    _logger.info("已恢复 baseline skill: %s", name)
                except OSError as e:
                    _logger.warning("恢复 baseline skill %s 失败: %s", name, e)


def _parse_mcp_stdio_command(raw: Any) -> list[str] | None:
    """解析 ``mcp.stdio_command``（config.user.json 原生数组或 JSON 字符串）。"""
    if not raw:
        return None
    spec: Any
    if isinstance(raw, list):
        spec = raw
    elif isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        spec = json.loads(text)
    else:
        return None
    if isinstance(spec, list) and len(spec) >= 1:
        return [str(x) for x in spec]
    return None


async def _register_mcp_tools_from_config(registry: Any) -> None:
    """若配置了 ``mcp.stdio_command``，连接 MCP stdio 并注册工具。"""
    mcp_raw = get_config("mcp.stdio_command", "")
    if not mcp_raw:
        return
    try:
        spec = _parse_mcp_stdio_command(mcp_raw)
        if not spec:
            _logger.warning(
                "mcp.stdio_command: 无效格式，需为非空 JSON 数组 [command, arg1, ...]"
            )
            return
        from miniagent.mcp.runtime import register_mcp_stdio_tools

        mcp_n = await register_mcp_stdio_tools(registry, spec[0], spec[1:])
        _logger.info("mcp.stdio_command: 已注册 %d 个 MCP 工具", mcp_n)
    except ImportError:
        _logger.warning(
            "mcp.stdio_command: 未安装 mcp 包，跳过（pip install miniagent-python[mcp]）"
        )
    except json.JSONDecodeError as e:
        _logger.warning("mcp.stdio_command: JSON 解析失败: %s", e)
    except Exception as e:
        _logger.warning("mcp.stdio_command: %s", e)


def _get_skills_root_for_baseline() -> str | None:
    """获取技能根目录路径（用于基线比较）；失败时返回 None。"""
    try:
        from miniagent.skills.paths import get_skills_root

        return get_skills_root()
    except Exception as e:
        _logger.debug("无法解析技能根目录（跳过 baseline 恢复）: %s", e)
        return None


__all__ = ["init_subsystems"]
