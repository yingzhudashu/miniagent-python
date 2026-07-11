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

from miniagent.infrastructure.logger import get_logger
from miniagent.memory.defaults import get_state_root
from miniagent.utils.session_id import safe_session_id

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
        from miniagent.infrastructure.trace_stats import remove_session_from_trace_files
    except ImportError:
        return 0
    return await asyncio.to_thread(remove_session_from_trace_files, session_key)


async def cleanup_background_session_artifacts(
    session_key: str,
    *,
    session_manager: Any | None = None,
    memory_store: Any | None = None,
    activity_log: Any | None = None,
    keyword_index: Any | None = None,
) -> None:
    """清除后台子 session 在磁盘与进程内的全部痕迹。

    在 Agent 回合结束且结果已写入 :class:`BackgroundTask` 后调用；
    不删除 ``BackgroundTaskManager`` 中的内存结果缓存。

    Args:
        session_key: 子 session 标识（``__bg__<task_id>``）
        session_manager: 会话管理器（可选）
        memory_store: 记忆存储（可选）
        activity_log: 活动日志（可选）
        keyword_index: 关键词索引（可选）
    """
    if not is_background_session_key(session_key):
        return

    safe_id = safe_session_id(session_key)
    state_root = get_state_root()

    try:
        from miniagent.engine.session_lock import release_session_lock

        release_session_lock(session_key)
    except Exception as exc:
        _logger.debug("释放后台 session 锁失败 (%s): %s", session_key, exc)

    if session_manager is not None:
        try:
            session_manager.destroy(session_key, keep_files=False)
        except Exception as exc:
            _logger.debug("destroy 后台 session 失败 (%s): %s", session_key, exc)

    workspace_path = os.path.join(state_root, "sessions", safe_id)
    await _remove_path_async(workspace_path, is_dir=True)

    memory_path = os.path.join(state_root, "memory", f"{safe_id}.json")
    await _remove_path_async(memory_path)

    if memory_store is not None:
        evict = getattr(memory_store, "evict_session", None)
        if callable(evict):
            try:
                evict(session_key)
            except Exception as exc:
                _logger.debug("驱逐记忆缓存失败 (%s): %s", session_key, exc)

    session_lt_path = os.path.join(state_root, "memory", "session_lt", f"{safe_id}.json")
    await _remove_path_async(session_lt_path)

    diary_dir = os.path.join(state_root, "memory", "diary", safe_id)
    await _remove_path_async(diary_dir, is_dir=True)

    try:
        from miniagent.memory.layered_memory import remove_agent_longterm_entries_for_session

        removed_agent = remove_agent_longterm_entries_for_session(session_key)
        if removed_agent:
            _logger.debug(
                "已从 agent_lt 移除后台 session 条目: %s (%d)",
                session_key,
                removed_agent,
            )
    except Exception as exc:
        _logger.debug("清理 agent_lt 失败 (%s): %s", session_key, exc)

    try:
        from miniagent.memory.shared_registry import get_registry

        registry = get_registry(state_root)
        remove_registry = getattr(registry, "remove_session_entries", None)
        removed_keys: list[str] = []
        if callable(remove_registry):
            removed_keys = remove_registry(session_key)
        if removed_keys and keyword_index is not None:
            remove_index = getattr(keyword_index, "remove_entry_keys", None)
            if callable(remove_index):
                remove_index(removed_keys)
        try:
            from miniagent.memory.embedding_search import get_embed_provider

            embed_provider = get_embed_provider(state_root)
            remove_embed = getattr(embed_provider.index, "remove_entry_keys", None)
            if removed_keys and callable(remove_embed):
                remove_embed(removed_keys)
        except Exception as exc:
            _logger.debug("清理 embedding 索引失败 (%s): %s", session_key, exc)
    except Exception as exc:
        _logger.debug("清理记忆注册表/索引失败 (%s): %s", session_key, exc)

    if activity_log is not None:
        remove_log = getattr(activity_log, "remove_session", None)
        if callable(remove_log):
            try:
                if asyncio.iscoroutinefunction(remove_log):
                    await remove_log(session_key)
                else:
                    await asyncio.to_thread(remove_log, session_key)
            except Exception as exc:
                _logger.debug("清理 activity log 失败 (%s): %s", session_key, exc)

    try:
        removed_traces = await _remove_session_trace_events(session_key)
        if removed_traces:
            _logger.debug("已移除后台 session trace 事件: %s (%d)", session_key, removed_traces)
    except Exception as exc:
        _logger.debug("清理 trace 失败 (%s): %s", session_key, exc)

    _logger.debug("已清理后台 session 痕迹: %s", session_key)


__all__ = [
    "cleanup_background_session_artifacts",
    "is_background_session_key",
]
