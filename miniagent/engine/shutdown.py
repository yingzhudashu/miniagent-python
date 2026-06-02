"""统一运行时关闭：定时任务、飞书 WS、消息队列、子进程、实例注册。

供 ``unified_main`` 正常返回、``.stop``、SIGINT/SIGTERM 等路径复用，避免仅 ``cancel`` 不 ``await``
导致飞书 ``finally`` / ``reset_feishu_ws_singleton`` 未执行。
"""

from __future__ import annotations

import asyncio
import logging

from miniagent.engine.cli_state import CliLoopState
from miniagent.infrastructure.instance import unregister_instance
from miniagent.infrastructure.process import cleanup_all_processes
from miniagent.runtime.context import RuntimeContext

_logger = logging.getLogger(__name__)


async def shutdown_runtime(
    ctx: RuntimeContext,
    state: CliLoopState,
    *,
    reason: str = "",
    abort_message_queues: bool = True,
    release_cli_session_lock: bool = True,
    call_unregister: bool = True,
) -> None:
    """优雅释放子系统（幂等、可重复调用）。

    顺序：登记的后台 job → 定时 ticker → 飞书 task（await 取消链）→ 可选 MQ abort
    → 子进程清理 → 会话锁 → 实例注销。

    注意：不再关闭默认线程池（``shutdown_default_executor``）。prompt_toolkit 的
    ``in_terminal()`` 异步上下文退出时仍会通过 ``run_in_executor`` 使用默认线程池，
    此处提前关闭会导致 ``"Executor shutdown has been called"``。线程池由进程退出自动回收。

    Args:
        ctx: 运行时上下文
        state: CLI 状态（会话 id 等）
        reason: 日志/排查用标签
        abort_message_queues: 是否对各 chat 队列调用 ``abort_chat``
        release_cli_session_lock: 是否 ``release_session_lock(active_session_id)``
        call_unregister: 是否 ``unregister_instance()``（若已在 ``run_cli_loop`` 末尾注销可传 False）
    """
    from miniagent.engine.session_lock import release_session_lock
    from miniagent.memory import dream_scheduler

    if reason:
        _logger.info("shutdown_runtime: begin (%s)", reason)

    # 1) tick_once / 其它登记在 ctx 上的 fire-and-forget
    snap_tracked = [t for t in ctx.shutdown_tracked_tasks if not t.done()]
    for t in snap_tracked:
        t.cancel()
    if snap_tracked:
        await asyncio.gather(*snap_tracked, return_exceptions=True)

    # 2) Dream 维护任务
    await dream_scheduler.cancel_pending_dream_tasks()

    # 3) 定时任务 ticker
    ev = ctx.scheduled_tasks_stop_event
    if ev is not None:
        ev.set()
    st_ticker = ctx.scheduled_tasks_ticker
    if st_ticker is not None and not st_ticker.done():
        st_ticker.cancel()
        try:
            await st_ticker
        except asyncio.CancelledError:
            pass

    # 3b) 技能目录监视
    sw_ev = ctx.skills_watch_stop_event
    if sw_ev is not None:
        sw_ev.set()
    sw_task = ctx.skills_watch_task
    if sw_task is not None and not sw_task.done():
        sw_task.cancel()
        try:
            await sw_task
        except asyncio.CancelledError:
            pass

    # 4) 飞书（await 取消以跑 poll_server / runtime 的 finally）
    fe = ctx.feishu
    stop_async = getattr(fe, "stop_async", None)
    if callable(stop_async):
        await stop_async()
    else:
        task = fe.get_task()
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            fe.set_task(None)

    try:
        from miniagent.feishu.poll_server import reset_feishu_ws_singleton

        await reset_feishu_ws_singleton()
    except Exception as e:
        _logger.debug("shutdown_runtime: reset_feishu_ws_singleton: %s", e)

    # 5) 消息队列
    if abort_message_queues:
        try:
            ctx.message_queue.abort_all_chats()
        except Exception as e:
            _logger.debug("shutdown_runtime: abort queues: %s", e)

    await cleanup_all_processes()

    # 5b) 关闭 httpx 客户端（飞书 drive_client）
    try:
        from miniagent.feishu.drive_client import close_http_client
        await close_http_client()
    except Exception as e:
        _logger.debug("shutdown_runtime: close_http_client: %s", e)

    if release_cli_session_lock:
        sid = (state.get("active_session_id") or "").strip()
        if sid:
            release_session_lock(sid)

    if call_unregister:
        try:
            unregister_instance()
        except Exception as e:
            _logger.debug("shutdown_runtime: unregister_instance: %s", e)

    # 6) 默认线程池：不再主动关闭。prompt_toolkit 的 in_terminal() 异步上下文退出时
    # 仍会通过 run_in_executor 使用默认线程池，此处提前关闭会导致
    # "Executor shutdown has been called"。由进程退出时自动清理即可。

    if reason:
        _logger.info("shutdown_runtime: done (%s)", reason)


__all__ = ["shutdown_runtime"]
