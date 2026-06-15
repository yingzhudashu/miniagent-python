"""统一运行时关闭：会话持久化、后台任务、飞书 WS、消息队列、子进程与网络资源。

供 ``unified_main`` 正常返回、``/stop``、SIGINT/SIGTERM 等路径复用，避免仅 ``cancel`` 不 ``await``
导致飞书 ``finally`` / ``reset_feishu_ws_singleton`` 未执行。

典型调用方参数（见 ``miniagent.engine.main``）：

- ``run_cli_loop`` 正常返回：``release_cli_session_lock=False``、``call_unregister=False``
  （外层 ``run_cli_loop`` 已处理锁与注销）
- ``/stop`` 与信号处理：两者均为 ``True``
"""

from __future__ import annotations

import asyncio
import logging

from miniagent.engine.cli_state import CliLoopState
from miniagent.infrastructure.instance import unregister_instance
from miniagent.infrastructure.process import cleanup_all_processes
from miniagent.runtime.context import RuntimeContext, reset_runtime_context_for_tests

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
    """优雅释放子系统（幂等、可重复调用；不抛异常，以免阻塞进程退出）。

    执行顺序：

    1. 保存 CLI 会话状态（``--continue``）
    2. 取消 ``ctx.shutdown_tracked_tasks`` 中登记的后台 job
    3. Dream 维护任务
    4. 定时任务 ticker
    5. 技能目录监视
    6. 飞书 task（``await`` 取消链）与 ``reset_feishu_ws_singleton``
    7. 可选：消息队列 ``abort_all_chats``
    8. 子进程清理
    9. HTTP 客户端、配置热更新、trace 写入器与过期文件 housekeeping
    10. 会话锁与实例注销

    注意：不再关闭默认线程池（``shutdown_default_executor``）。prompt_toolkit 的
    ``in_terminal()`` 异步上下文退出时仍会通过 ``run_in_executor`` 使用默认线程池，
    此处提前关闭会导致 ``"Executor shutdown has been called"``。线程池由进程退出自动回收。

    Args:
        ctx: 运行时上下文
        state: CLI 状态（会话 id 等）
        reason: 日志/排查用标签
        abort_message_queues: 是否调用 ``message_queue.abort_all_chats()``
        release_cli_session_lock: 是否 ``release_session_lock(active_session_id)``
        call_unregister: 是否 ``unregister_instance()``（若已在 ``run_cli_loop`` 末尾注销可传 False）

    Returns:
        None。各步骤失败时仅记录 debug 日志，不向外抛出。
    """
    if reason:
        _logger.info("shutdown_runtime: begin (%s)", reason)

    try:
        from miniagent.engine.session_continue import save_cli_session_state

        save_cli_session_state(ctx, state)
    except Exception as e:
        _logger.debug("shutdown_runtime: save_cli_session_state: %s", e)

    from miniagent.engine.session_lock import release_session_lock

    # 1) tick_once / 其它登记在 ctx 上的 fire-and-forget
    snap_tracked = [t for t in ctx.shutdown_tracked_tasks if not t.done()]
    for t in snap_tracked:
        t.cancel()
    if snap_tracked:
        await asyncio.gather(*snap_tracked, return_exceptions=True)

    # 2) Dream 维护任务
    try:
        from miniagent.memory import dream_scheduler

        await dream_scheduler.cancel_pending_dream_tasks()
    except Exception as e:
        _logger.debug("shutdown_runtime: cancel_pending_dream_tasks: %s", e)

    # 3) 定时任务 ticker
    ev = ctx.scheduled_tasks_stop_event
    if ev is not None:
        ev.set()
    st_ticker = ctx.scheduled_tasks_ticker
    if st_ticker is not None and not st_ticker.done():
        st_ticker.cancel()
        try:
            await st_ticker
        except asyncio.CancelledError as e:
            _logger.debug("定时任务 ticker 取消: %s", e)

    # 3b) 技能目录监视
    sw_ev = ctx.skills_watch_stop_event
    if sw_ev is not None:
        sw_ev.set()
    sw_task = ctx.skills_watch_task
    if sw_task is not None and not sw_task.done():
        sw_task.cancel()
        try:
            await sw_task
        except asyncio.CancelledError as e:
            _logger.debug("技能目录监视任务取消: %s", e)

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
            except asyncio.CancelledError as e:
                _logger.debug("飞书任务取消: %s", e)
            finally:
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

    try:
        await cleanup_all_processes()
    except Exception as e:
        _logger.debug("shutdown_runtime: cleanup_all_processes: %s", e)

    # 5b) 关闭 httpx 客户端（飞书 drive_client）
    try:
        from miniagent.feishu.drive_client import close_http_client

        await close_http_client()
    except Exception as e:
        _logger.debug("shutdown_runtime: close_http_client: %s", e)

    # 5c) 关闭 embedding HTTP 客户端（网络可靠性）
    try:
        from miniagent.memory.embedding_search import close_embed_http_client

        await close_embed_http_client()
    except Exception as e:
        _logger.debug("shutdown_runtime: close_embed_http_client: %s", e)

    # 5d) 关闭 ClawHub HTTP 客户端（网络可靠性）
    try:
        from miniagent.skills.clawhub_client import close_clawhub_client

        await close_clawhub_client()
    except Exception as e:
        _logger.debug("shutdown_runtime: close_clawhub_client: %s", e)

    # 5e) 停止配置热更新监听（用户体验增强）
    try:
        from miniagent.infrastructure.config_watch import stop_config_watch

        stop_config_watch(ctx)
    except Exception as e:
        _logger.debug("shutdown_runtime: stop_config_watch: %s", e)

    # 5f) 关闭trace异步写入器（确保trace事件不丢失）
    try:
        from miniagent.infrastructure.tracing import shutdown_trace_writer

        shutdown_trace_writer()
    except Exception as e:
        _logger.debug("shutdown_runtime: shutdown_trace_writer: %s", e)

    # 5g) 清理过期trace文件（可选）
    try:
        from miniagent.infrastructure.json_config import get_config
        from miniagent.infrastructure.trace_stats import cleanup_old_traces

        if get_config("trace.auto_cleanup", True):
            retention_days = get_config("trace.retention_days", 7)
            deleted_count = cleanup_old_traces(retention_days)
            if deleted_count > 0:
                _logger.info("shutdown: 清理过期trace文件 %d 个", deleted_count)
    except Exception as e:
        _logger.debug("shutdown_runtime: cleanup_old_traces: %s", e)

    # 5h) 清理过期自我优化提案
    try:
        from miniagent.core.self_opt.proposal_store import ProposalStore
        from miniagent.infrastructure.json_config import get_config

        retention_days = int(get_config("self_optimization.proposal_retention_days", 30))
        deleted_proposals = ProposalStore.cleanup_old_proposals(retention_days)
        if deleted_proposals > 0:
            _logger.info("shutdown: 清理过期提案文件 %d 个", deleted_proposals)
    except Exception as e:
        _logger.debug("shutdown_runtime: cleanup_old_proposals: %s", e)

    if release_cli_session_lock:
        sid = (state.get("active_session_id") or "").strip()
        if sid:
            try:
                release_session_lock(sid)
            except Exception as e:
                _logger.debug("shutdown_runtime: release_session_lock: %s", e)

    if call_unregister:
        try:
            unregister_instance()
        except Exception as e:
            _logger.debug("shutdown_runtime: unregister_instance: %s", e)

    # 6) 默认线程池：不再主动关闭。prompt_toolkit 的 in_terminal() 异步上下文退出时
    # 仍会通过 run_in_executor 使用默认线程池，此处提前关闭会导致
    # "Executor shutdown has been called"。由进程退出时自动清理即可。

    reset_runtime_context_for_tests()

    if reason:
        _logger.info("shutdown_runtime: done (%s)", reason)


__all__ = ["shutdown_runtime"]
