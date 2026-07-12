"""消息队列与并行会话配置的启动接线。

本模块负责将 ``agent.parallel_sessions`` 映射到 :class:`~miniagent.infrastructure.message_queue.MessageQueueManager`
的跨队列串行策略；引擎层的 per-session 并行与限流由
:class:`~miniagent.engine.session_exec.SessionExecCoordinator` 单独处理（``agent.max_parallel_sessions``）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from miniagent.infrastructure.json_config import get_config_bool

if TYPE_CHECKING:
    from miniagent.infrastructure.channel_router import ChannelRouter
    from miniagent.infrastructure.message_queue import MessageQueueManager


def configure_message_queue_for_parallel(message_queue: MessageQueueManager) -> None:
    """根据 ``agent.parallel_sessions`` 配置消息队列跨队列串行行为。

    - ``parallel_sessions=true``（默认）：``cross_queue_serial=False``，不持有 ``exec_lock``，
      不同 ``chat_id`` 可并行投递；同一 ``chat_id`` 仍由各自队列串行。
    - ``parallel_sessions=false``：``cross_queue_serial=True`` 并确保 ``exec_lock`` 存在，
      CLI 与飞书等通道按全局 FIFO 串行。

    与 :class:`~miniagent.engine.session_exec.SessionExecCoordinator` 的分工：
    本函数仅约束**消息入队/出队**是否跨 ``chat_id`` 全局排序；协调器约束**Agent 执行**
    是否按 ``session_key`` 并行及并发上限。
    """
    parallel = get_config_bool("agent.parallel_sessions", True)
    message_queue.cross_queue_serial = not parallel
    if parallel:
        message_queue.exec_lock = None
    else:
        message_queue.ensure_exec_lock()


def resolve_active_session_key(
    channel_router: ChannelRouter | None,
    fallback: str = "default",
) -> str:
    """解析 CLI 当前应使用的 ``session_key``。

    通过 ``channel_router.resolve("__cli__")`` 读取 CLI 通道绑定；未绑定时
    :class:`~miniagent.infrastructure.channel_router.ChannelRouter` 返回 ``"__cli__"`` 本身。

    Args:
        channel_router: 通道路由器；为 ``None`` 时使用 *fallback*。
        fallback: 仅在 ``channel_router`` 不可用或 ``resolve`` 抛异常时的备用
            ``session_key``（通常为 ``state["active_session_id"]``）。路由器正常时
            **不会**使用此值。

    Returns:
        当前 CLI 会话键。
    """
    if channel_router is None:
        return fallback
    try:
        from miniagent.infrastructure.channel_router import ChannelRouter

        return channel_router.resolve(ChannelRouter.CLI_CHANNEL)
    except Exception:
        return fallback


__all__ = ["configure_message_queue_for_parallel", "resolve_active_session_key"]
