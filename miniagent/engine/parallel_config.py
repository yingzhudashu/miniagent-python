"""消息队列与并行会话配置的启动接线。"""

from __future__ import annotations

from typing import Any

from miniagent.infrastructure.json_config import get_config


def configure_message_queue_for_parallel(message_queue: Any) -> None:
    """根据 ``agent.parallel_sessions`` 配置消息队列跨队列串行行为。"""
    parallel = bool(get_config("agent.parallel_sessions", True))
    message_queue.cross_queue_serial = not parallel
    if not parallel:
        message_queue.ensure_exec_lock()


def resolve_active_session_key(channel_router: Any, fallback: str = "default") -> str:
    """解析 CLI 当前绑定的 session_key。"""
    if channel_router is None:
        return fallback
    try:
        return channel_router.resolve("__cli__")
    except Exception:
        return fallback


__all__ = ["configure_message_queue_for_parallel", "resolve_active_session_key"]
