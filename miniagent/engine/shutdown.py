"""统一运行时关闭：会话持久化、后台任务、飞书 WS、消息队列、子进程与网络资源。

供 ``run_runtime`` 正常返回、``/stop``、SIGINT/SIGTERM 等路径复用，避免仅 ``cancel`` 不 ``await``
导致飞书 task ``finally`` / ``FeishuPollState.reset`` 未执行。

典型调用方参数（见 ``miniagent.engine.main``）：

- ``run_cli_loop`` 正常返回：``release_cli_session_lock=False``、``call_unregister=False``
  （外层 ``run_cli_loop`` 已处理锁与注销）
- ``/stop`` 与信号处理：两者均为 ``True``
"""

from __future__ import annotations

import asyncio
import logging
import time

from miniagent.bootstrap.application import ApplicationContainer
from miniagent.engine.cli_state import CliLoopState
from miniagent.infrastructure.instance import unregister_instance
from miniagent.infrastructure.process import cleanup_all_processes

_logger = logging.getLogger(__name__)


async def shutdown_runtime(
    ctx: ApplicationContainer,
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
    2. 生命周期图（skills watcher → ticker → 飞书 → config watcher）停止生产新任务
    3. 取消登记的后台 job，并关闭 `/btw` 后台任务管理器
    4. 可选：取消并等待消息队列任务
    5. 停止 Dream 维护任务并关闭 memory 网络资源
    6. 子进程清理
    7. 记忆索引、其余 HTTP 客户端、trace 写入器与过期文件 housekeeping
    8. 会话锁与实例注销

    注意：不再关闭默认线程池（``shutdown_default_executor``）。prompt_toolkit 的
    ``in_terminal()`` 异步上下文退出时仍会通过 ``run_in_executor`` 使用默认线程池，
    此处提前关闭会导致 ``"Executor shutdown has been called"``。线程池由进程退出自动回收。

    Args:
        ctx: 运行时上下文
        state: CLI 状态（会话 id 等）
        reason: 日志/排查用标签
        abort_message_queues: 是否取消并等待消息队列中的全部任务
        release_cli_session_lock: 是否 ``release_session_lock(active_session_id)``
        call_unregister: 是否 ``unregister_instance()``（若已在 ``run_cli_loop`` 末尾注销可传 False）

    Returns:
        None。各步骤失败时仅记录 debug 日志，不向外抛出。
    """
    if reason:
        _logger.info("shutdown_runtime: begin (%s)", reason)

    from miniagent.infrastructure.tracing import emit_trace, new_trace_id

    shutdown_span_id = new_trace_id("span")
    shutdown_wall_start = time.monotonic_ns()
    shutdown_cpu_start = time.process_time_ns()
    emit_trace(
        {
            "type": "agent.phase_start",
            "phase": "shutdown",
            "span_id": shutdown_span_id,
        }
    )

    try:
        from miniagent.engine.session_continue import save_cli_session_state

        await asyncio.to_thread(save_cli_session_state, ctx, state)
    except Exception as e:
        _logger.debug("shutdown_runtime: save_cli_session_state: %s", e)

    from miniagent.engine.session_lock import release_session_lock

    # 1) 先停止静态生产者，避免取消任务快照后 ticker / Feishu 又提交新工作。
    lifecycle_manager = ctx.lifecycle_manager
    if lifecycle_manager is not None:
        try:
            await lifecycle_manager.stop()
        except Exception as e:
            _logger.debug("shutdown_runtime: lifecycle manager stop: %s", e)

    # 2) tick_once / 其它登记在 ctx 上的 fire-and-forget
    snap_tracked = [t for t in ctx.shutdown_tracked_tasks if not t.done()]
    for t in snap_tracked:
        t.cancel()
    if snap_tracked:
        await asyncio.gather(*snap_tracked, return_exceptions=True)

    # 3) 容器持有的 /btw 管理器（取消执行任务并等待子 session finally）
    try:
        await ctx.background_tasks.shutdown()
    except Exception as e:
        _logger.debug("shutdown_runtime: background task manager shutdown: %s", e)

    # 4) 停止通道后取消并等待队列任务，确保它们不会继续使用后续关闭的资源。
    if abort_message_queues:
        try:
            await ctx.message_queue.shutdown()
        except Exception as e:
            _logger.debug("shutdown_runtime: message queue shutdown: %s", e)

    # 5) 队列消费者结束后再关闭 memory 的维护任务与 embedding 连接池。
    try:
        await ctx.memory.shutdown()
    except Exception as e:
        _logger.debug("shutdown_runtime: memory runtime shutdown: %s", e)

    # MCP owns stdio/session async contexts outside the chat queue. Close them
    # before the generic child-process fallback so protocol shutdown can run.
    try:
        from miniagent.mcp.runtime import close_mcp_connections

        await close_mcp_connections()
    except Exception as e:
        _logger.debug("shutdown_runtime: MCP connections close: %s", e)

    try:
        await cleanup_all_processes()
    except Exception as e:
        _logger.debug("shutdown_runtime: cleanup_all_processes: %s", e)

    # 5a) 记忆运行时由 ApplicationContainer 独占；退出前统一持久化全部派生索引。
    try:
        await asyncio.to_thread(ctx.memory.close)
    except Exception as e:
        _logger.debug("shutdown_runtime: memory runtime close: %s", e)

    # Close the active pool and pools retired by hot reload. Retired pools stay
    # alive until this point so in-flight turns using an old client can finish.
    try:
        from miniagent.core.openai_client import close_async_openai_client

        clients = [ctx.openai_client, *ctx.retired_openai_clients]
        ctx.openai_client = None
        ctx.retired_openai_clients.clear()
        seen_clients: set[int] = set()
        for client in clients:
            if client is None or id(client) in seen_clients:
                continue
            seen_clients.add(id(client))
            try:
                await close_async_openai_client(client)
            except Exception as error:
                _logger.debug("shutdown_runtime: OpenAI client close: %s", error)
    except Exception as e:
        _logger.debug("shutdown_runtime: OpenAI clients close: %s", e)

    # 5b) 关闭 httpx 客户端（飞书 drive_client）
    try:
        from miniagent.feishu.drive_client import close_http_client

        await close_http_client()
    except Exception as e:
        _logger.debug("shutdown_runtime: close_http_client: %s", e)

    try:
        from miniagent.tools.html_upload import close_html_upload_http_clients

        await close_html_upload_http_clients()
    except Exception as e:
        _logger.debug("shutdown_runtime: close_html_upload_http_clients: %s", e)

    try:
        from miniagent.infrastructure.httpx_pool import close_shared_httpx_clients

        await close_shared_httpx_clients()
    except Exception as e:
        _logger.debug("shutdown_runtime: close_shared_httpx_clients: %s", e)

    try:
        from miniagent.infrastructure.browser_pool import close_browser_pool

        await close_browser_pool()
    except Exception as e:
        _logger.debug("shutdown_runtime: close_browser_pool: %s", e)

    # 5c) 关闭容器所拥有的 ClawHub HTTP 客户端。
    try:
        if ctx.clawhub is not None:
            await ctx.clawhub.close()
    except Exception as e:
        _logger.debug("shutdown_runtime: ClawHub client close: %s", e)

    # 5g) 清理过期trace文件（可选）
    try:
        from miniagent.infrastructure.json_config import get_config
        from miniagent.infrastructure.trace_stats import cleanup_old_traces

        if get_config("trace.auto_cleanup", True):
            retention_days = get_config("trace.retention_days", 7)
            deleted_count = await asyncio.to_thread(
                cleanup_old_traces,
                retention_days,
            )
            if deleted_count > 0:
                _logger.info("shutdown: 清理过期trace文件 %d 个", deleted_count)
    except Exception as e:
        _logger.debug("shutdown_runtime: cleanup_old_traces: %s", e)

    # 5h) 清理过期自我优化提案
    try:
        from miniagent.core.self_opt.proposal_store import ProposalStore
        from miniagent.infrastructure.json_config import get_config

        retention_days = int(get_config("self_optimization.proposal_retention_days", 30))
        deleted_proposals = await asyncio.to_thread(
            ProposalStore.cleanup_old_proposals,
            retention_days,
        )
        if deleted_proposals > 0:
            _logger.info("shutdown: 清理过期提案文件 %d 个", deleted_proposals)
    except Exception as e:
        _logger.debug("shutdown_runtime: cleanup_old_proposals: %s", e)

    if release_cli_session_lock:
        sid = (state.get("active_session_id") or "").strip()
        if sid:
            try:
                await asyncio.to_thread(release_session_lock, sid)
            except Exception as e:
                _logger.debug("shutdown_runtime: release_session_lock: %s", e)

    if call_unregister:
        try:
            await asyncio.to_thread(unregister_instance)
        except Exception as e:
            _logger.debug("shutdown_runtime: unregister_instance: %s", e)

    # Trace is the final owned resource: record the complete shutdown phase,
    # then deterministically drain the writer after all producers are stopped.
    emit_trace(
        {
            "type": "agent.phase_end",
            "phase": "shutdown",
            "span_id": shutdown_span_id,
            "duration_ms": (time.monotonic_ns() - shutdown_wall_start) / 1_000_000,
            "cpu_ms": (time.process_time_ns() - shutdown_cpu_start) / 1_000_000,
            "success": True,
        }
    )
    try:
        from miniagent.infrastructure.tracing import shutdown_trace_writer

        await asyncio.to_thread(shutdown_trace_writer)
    except Exception as e:
        _logger.debug("shutdown_runtime: shutdown_trace_writer: %s", e)

    # 6) 默认线程池：不再主动关闭。prompt_toolkit 的 in_terminal() 异步上下文退出时
    # 仍会通过 run_in_executor 使用默认线程池，此处提前关闭会导致
    # "Executor shutdown has been called"。由进程退出时自动清理即可。

    if reason:
        _logger.info("shutdown_runtime: done (%s)", reason)


__all__ = ["shutdown_runtime"]
