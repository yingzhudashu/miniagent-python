"""Engine — 子系统初始化

拆分自 unified.py。在 ``unified_main`` 早期调用 ``init_subsystems``，完成 **工具与技能**
的就绪后再进入 CLI 循环。

顺序要点（与注册覆盖语义一致）：

1. ``register_builtin_tools``：内置 ``ALL_TOOLS``（含 session_memory_tools）
2. 磁盘技能包发现并注册工具（同名冲突时跳过技能侧）
3. 可选 ``mcp.stdio_command`` / ``mcp.stdio_env``：stdio MCP 工具（未安装 ``mcp`` 包则打日志跳过）
4. ``build_skill_snapshots``（内含 ``BUILTIN_TOOLBOXES`` 合并）；若已注册 MCP 工具则追加 ``mcp`` 工具箱；创建 ``SessionManager``；默认会话加锁；
   ``KeywordIndex.prune_expired`` 清理过期索引项

配置见 config.defaults.json；架构见 ``docs/ARCHITECTURE.md``。
"""

from __future__ import annotations

import asyncio
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

    from miniagent.core.config import get_default_agent_config

    try:
        agent_cfg = get_default_agent_config()
    except Exception:
        agent_cfg = None

    loaded_skills, _added, _removed = await bootstrap_skill_packages(
        registry, skill_registry, config=agent_cfg,
    )

    await _register_mcp_tools_from_config(registry)

    # 2. 获取工具箱和系统提示（含 gating）；MCP 工具注册后追加 mcp 工具箱供规划器选用
    skill_toolboxes, skill_prompts = build_skill_snapshots(skill_registry, agent_cfg)
    from miniagent.mcp.toolbox import ensure_mcp_toolbox

    skill_toolboxes = ensure_mcp_toolbox(skill_toolboxes, registry)

    # 3. 创建 SessionManager
    session_manager = SessionManager(registry, skill_toolboxes, loaded_skills, clawhub=clawhub)

    # 4. 创建默认会话并加锁
    session_name = env_str("MINIAGENT_SESSION_NAME") or get_config("session.default_name", None)
    active_session_id = await _init_default_session_async(
        session_manager, channel_router, session_name=session_name
    )

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


def _resolve_startup_session_id(
    session_manager: Any,
    channel_router: Any,
    *,
    session_name: str | None = None,
) -> str:
    """解析启动时应使用的会话 ID（不含 get_or_create / 加锁）。"""
    continue_mode = get_config("session.continue_mode", False) or env_flag(
        "MINIAGENT_CONTINUE_SESSION"
    )

    if continue_mode and not session_name:
        return _resolve_continue_session_id(session_manager, channel_router)
    if session_name:
        return session_name
    return "default"


async def _init_default_session_async(
    session_manager: Any,
    channel_router: Any,
    *,
    session_name: str | None = None,
) -> str:
    """创建默认会话并加锁（磁盘恢复在后台线程执行，避免阻塞事件循环）。"""
    from miniagent.engine.session_lock import try_lock_session
    from miniagent.session.manager import SessionOptions

    session_id = _resolve_startup_session_id(
        session_manager, channel_router, session_name=session_name
    )

    await asyncio.to_thread(
        session_manager.get_or_create,
        session_id,
        SessionOptions(description="默认会话"),
    )
    channel_router.bind("__cli__", session_id)
    channel_router.set_primary(session_id)

    ok, reason = try_lock_session(session_id)
    if ok:
        return session_id

    fallback = f"{session_id}-{random.randint(1000, 9999)}"
    _logger.info(
        "会话 %s 加锁失败%s，回退到 %s",
        session_id,
        f" ({reason})" if reason else "",
        fallback,
    )
    await asyncio.to_thread(
        session_manager.get_or_create,
        fallback,
        SessionOptions(description="默认会话（回退）"),
    )
    channel_router.bind("__cli__", fallback)
    channel_router.set_primary(fallback)
    fb_ok, fb_reason = try_lock_session(fallback)
    if not fb_ok:
        _logger.warning("回退会话 %s 加锁失败: %s", fallback, fb_reason)
    return fallback


def _init_default_session(session_manager: Any, channel_router: Any, *, session_name: str | None = None) -> str:
    """创建默认会话并加锁（同步；供测试与非 async 调用方）。"""
    from miniagent.engine.session_lock import try_lock_session
    from miniagent.session.manager import SessionOptions

    session_id = _resolve_startup_session_id(
        session_manager, channel_router, session_name=session_name
    )

    session_manager.get_or_create(session_id, SessionOptions(description="默认会话"))
    channel_router.bind("__cli__", session_id)
    channel_router.set_primary(session_id)

    ok, reason = try_lock_session(session_id)
    if ok:
        return session_id

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


def _parse_mcp_stdio_env(raw: Any) -> dict[str, str] | None:
    """解析 ``mcp.stdio_env``（原生对象或 JSON 字符串）为 ``str -> str`` 环境变量表。"""
    if not raw:
        return None
    spec: Any
    if isinstance(raw, dict):
        spec = raw
    elif isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        spec = json.loads(text)
    else:
        return None
    if not isinstance(spec, dict):
        return None
    return {str(k): str(v) for k, v in spec.items()}


def _is_mcp_missing_error(exc: BaseException) -> bool:
    """是否为「未安装 mcp 包」类错误（``ImportError`` 或包装的 ``RuntimeError``）。"""
    if isinstance(exc, ImportError):
        return True
    return isinstance(exc, RuntimeError) and "未安装 mcp" in str(exc)


async def _register_mcp_tools_from_config(registry: Any) -> int:
    """若配置了 ``mcp.stdio_command``，连接 MCP stdio 并注册工具。

    Returns:
        成功注册的 MCP 工具数量（未配置或失败时为 0）
    """
    mcp_raw = get_config("mcp.stdio_command", "")
    if not mcp_raw:
        return 0
    try:
        spec = _parse_mcp_stdio_command(mcp_raw)
        if not spec:
            _logger.warning(
                "mcp.stdio_command: 无效格式，需为非空 JSON 数组 [command, arg1, ...]"
            )
            return 0
        env_raw = get_config("mcp.stdio_env", None)
        try:
            stdio_env = _parse_mcp_stdio_env(env_raw)
        except json.JSONDecodeError as e:
            _logger.warning("mcp.stdio_env: JSON 解析失败: %s", e)
            return 0
        if env_raw and stdio_env is None:
            _logger.warning("mcp.stdio_env: 无效格式，需为 JSON 对象 {\"KEY\": \"value\", ...}")
            return 0

        from miniagent.mcp.runtime import register_mcp_stdio_tools

        mcp_n = await register_mcp_stdio_tools(
            registry, spec[0], spec[1:], env=stdio_env,
        )
        _logger.info("mcp.stdio_command: 已注册 %d 个 MCP 工具", mcp_n)
        return mcp_n
    except json.JSONDecodeError as e:
        _logger.warning("mcp.stdio_command: JSON 解析失败: %s", e)
    except RuntimeError as e:
        if _is_mcp_missing_error(e):
            _logger.warning(
                "mcp.stdio_command: 未安装 mcp 包，跳过（pip install miniagent-python[mcp]）"
            )
        else:
            _logger.warning("mcp.stdio_command: %s", e)
    except Exception as e:
        _logger.warning("mcp.stdio_command: %s", e)
    return 0


def _get_skills_root_for_baseline() -> str | None:
    """获取技能根目录路径（用于基线比较）；失败时返回 None。"""
    try:
        from miniagent.skills.paths import get_skills_root

        return get_skills_root()
    except Exception as e:
        _logger.debug("无法解析技能根目录（跳过 baseline 恢复）: %s", e)
        return None


__all__ = ["init_subsystems"]
