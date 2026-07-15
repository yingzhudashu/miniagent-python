"""后台子 session 执行完成后的磁盘与进程内痕迹清理。

清理范围：会话工作区、记忆 JSON、session_lt、diary、agent_lt 来源条目、
memory-registry / keyword-index / embedding-index、全部日期的 activity log、
全部 trace 分片，以及进程内 session 锁与 MemoryStore 缓存。
"""

from __future__ import annotations

import asyncio
import os
import shutil
from typing import Any

from miniagent.agent.logging import get_logger
from miniagent.agent.ports.memory import MemoryRuntimeProtocol
from miniagent.assistant.infrastructure.paths import resolve_state_dir
from miniagent.assistant.utils.session_id import safe_session_id

_logger = get_logger(__name__)

_BG_SESSION_PREFIX = "__bg__"


def is_background_session_key(session_key: str) -> bool:
    """判断是否为后台任务子 session 标识。"""
    return (session_key or "").startswith(_BG_SESSION_PREFIX)


async def _remove_path_async(path: str, *, is_dir: bool = False) -> None:
    if is_dir:
        if os.path.isdir(path):
            await asyncio.to_thread(shutil.rmtree, path, True)
        return
    if os.path.isfile(path):
        await asyncio.to_thread(os.remove, path)


async def _remove_session_trace_events(session_key: str) -> int:
    """从全部 trace jsonl 分片中移除指定 session 的事件行。"""
    try:
        from miniagent.assistant.infrastructure.trace_stats import (
            remove_completed_session_from_trace_files,
        )
    except ImportError:
        return 0
    return await asyncio.to_thread(remove_completed_session_from_trace_files, session_key)


async def _release_background_session_lock(session_key: str) -> None:
    """尽力释放后台会话锁；锁模块不可用或重复释放均不阻断清理。"""
    try:
        from miniagent.assistant.engine.session_lock import release_session_lock

        await asyncio.to_thread(release_session_lock, session_key)
    except Exception as error:
        _logger.debug("释放后台 session 锁失败 (%s): %s", session_key, error, exc_info=True)


async def _forget_background_session(session_key: str, session_manager: Any | None) -> None:
    """从会话管理器缓存移除会话，必要时回退到销毁旧接口。"""
    if session_manager is None:
        return
    try:
        forget_session = getattr(session_manager, "forget_session", None)
        if callable(forget_session):
            forget_session(session_key)
        else:
            await asyncio.to_thread(
                session_manager.destroy,
                session_key,
                keep_files=False,
            )
    except Exception as error:
        _logger.debug("destroy 后台 session 失败 (%s): %s", session_key, error, exc_info=True)


async def _remove_background_memory_entries(
    session_key: str,
    memory: MemoryRuntimeProtocol | None,
) -> None:
    """清理进程缓存、注册表和派生索引；各存储失败互不影响。"""
    if memory is None:
        return
    evict = getattr(memory.store, "evict_session", None)
    if callable(evict):
        try:
            evict(session_key)
        except Exception as error:
            _logger.debug("驱逐记忆缓存失败 (%s): %s", session_key, error, exc_info=True)
    try:
        await asyncio.to_thread(memory.remove_session_entries, session_key)
    except Exception as error:
        _logger.debug("清理记忆注册表/索引失败 (%s): %s", session_key, error, exc_info=True)


async def _remove_background_activity_log(
    session_key: str,
    memory: MemoryRuntimeProtocol | None,
) -> None:
    """兼容同步与异步 activity log 仓储的会话清理接口。"""
    if memory is None:
        return
    remove_log = getattr(memory.activity_log, "remove_session", None)
    if not callable(remove_log):
        return
    try:
        if asyncio.iscoroutinefunction(remove_log):
            await remove_log(session_key)
        else:
            await asyncio.to_thread(remove_log, session_key)
    except Exception as error:
        _logger.debug("清理 activity log 失败 (%s): %s", session_key, error, exc_info=True)


async def _remove_background_agent_memory(session_key: str) -> None:
    """删除 agent 长期记忆中由后台会话产生的来源条目。"""
    try:
        from miniagent.assistant.memory.layered_memory import (
            remove_agent_longterm_entries_for_session,
        )

        removed = await asyncio.to_thread(remove_agent_longterm_entries_for_session, session_key)
        if removed:
            _logger.debug("已从 agent_lt 移除后台 session 条目: %s (%d)", session_key, removed)
    except Exception as error:
        _logger.debug("清理 agent_lt 失败 (%s): %s", session_key, error, exc_info=True)


async def _remove_background_traces(session_key: str) -> None:
    """从所有 trace 分片移除后台会话事件。"""
    try:
        removed = await _remove_session_trace_events(session_key)
        if removed:
            _logger.debug("已移除后台 session trace 事件: %s (%d)", session_key, removed)
    except Exception as error:
        _logger.debug("清理 trace 失败 (%s): %s", session_key, error, exc_info=True)


async def cleanup_background_session_artifacts(
    session_key: str,
    *,
    session_manager: Any | None = None,
    memory: MemoryRuntimeProtocol | None = None,
) -> None:
    """清除后台子 session 在磁盘与进程内的全部痕迹。

    在 Agent 回合结束且结果已写入 :class:`BackgroundTask` 后调用；
    不删除 ``BackgroundTaskManager`` 中的内存结果缓存。

    Args:
        session_key: 子 session 标识（``__bg__<task_id>``）
        session_manager: 会话管理器（可选）
        memory: 应用记忆运行时；提供时同步清理缓存、注册表和派生索引
    """
    if not is_background_session_key(session_key):
        return

    safe_id = safe_session_id(session_key)
    state_root = memory.state_root if memory is not None else resolve_state_dir()

    await _release_background_session_lock(session_key)
    await _forget_background_session(session_key, session_manager)

    workspace_path = os.path.join(state_root, "sessions", safe_id)
    await _remove_path_async(workspace_path, is_dir=True)

    memory_path = os.path.join(state_root, "memory", f"{safe_id}.json")
    await _remove_path_async(memory_path)

    session_lt_path = os.path.join(state_root, "memory", "session_lt", f"{safe_id}.json")
    await _remove_path_async(session_lt_path)

    diary_dir = os.path.join(state_root, "memory", "diary", safe_id)
    await _remove_path_async(diary_dir, is_dir=True)

    await _remove_background_agent_memory(session_key)
    await _remove_background_memory_entries(session_key, memory)
    await _remove_background_activity_log(session_key, memory)
    await _remove_background_traces(session_key)

    _logger.debug("已清理后台 session 痕迹: %s", session_key)


__all__ = [
    "cleanup_background_session_artifacts",
    "is_background_session_key",
]
