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


async def _shutdown_step(label: str, awaitable) -> None:
    """执行一个关闭 awaitable；失败仅记录上下文，不阻断后续资源释放。"""
    try:
        await awaitable
    except Exception as error:
        _logger.debug("shutdown_runtime: %s: %s", label, error, exc_info=True)


async def _shutdown_thread_step(label: str, callable_, *args) -> None:
    """在线程中执行同步关闭步骤并统一降级。"""
    await _shutdown_step(label, asyncio.to_thread(callable_, *args))


async def _stop_runtime_work(
    ctx: ApplicationContainer,
    *,
    abort_message_queues: bool,
) -> None:
    """停止任务生产者、排空已登记任务与队列消费者。"""
    if ctx.lifecycle_manager is not None:
        await _shutdown_step("lifecycle manager stop", ctx.lifecycle_manager.stop())
    tracked = [task for task in ctx.shutdown_tracked_tasks if not task.done()]
    for task in tracked:
        task.cancel()
    if tracked:
        await asyncio.gather(*tracked, return_exceptions=True)
    await _shutdown_step("background task manager shutdown", ctx.background_tasks.shutdown())
    if abort_message_queues:
        await _shutdown_step("message queue shutdown", ctx.message_queue.shutdown())
    await _shutdown_step("memory runtime shutdown", ctx.memory.shutdown())


async def _close_protocol_and_process_resources() -> None:
    """先关闭协议上下文，再执行通用子进程兜底清理。"""
    try:
        from miniagent.mcp.runtime import close_mcp_connections

        await _shutdown_step("MCP connections close", close_mcp_connections())
    except ImportError as error:
        _logger.debug("shutdown_runtime: MCP runtime unavailable: %s", error)
    await _shutdown_step("cleanup_all_processes", cleanup_all_processes())


async def _close_openai_clients(ctx: ApplicationContainer) -> None:
    """去重关闭当前/退休 gateway 与嵌入式 v2 OpenAI 客户端。"""
    from miniagent.core.openai_client import close_async_openai_client

    gateways = [ctx.llm_gateway, *ctx.retired_llm_gateways]
    ctx.llm_gateway = None
    ctx.retired_llm_gateways.clear()
    seen: set[int] = set()
    for gateway in gateways:
        if gateway is None or id(gateway) in seen:
            continue
        seen.add(id(gateway))
        await _shutdown_step("LLM gateway close", gateway.close())
    clients = [ctx.openai_client, *ctx.retired_openai_clients]
    ctx.openai_client = None
    ctx.retired_openai_clients.clear()
    for client in clients:
        if client is None or id(client) in seen:
            continue
        seen.add(id(client))
        await _shutdown_step("OpenAI client close", close_async_openai_client(client))


async def _close_network_resources(ctx: ApplicationContainer) -> None:
    """关闭应用拥有的 HTTP、浏览器与 ClawHub 资源。"""
    try:
        await _close_openai_clients(ctx)
    except Exception as error:
        _logger.debug("shutdown_runtime: OpenAI clients close: %s", error, exc_info=True)
    from miniagent.feishu.drive_client import close_http_client
    from miniagent.infrastructure.browser_pool import close_browser_pool
    from miniagent.infrastructure.httpx_pool import close_shared_httpx_clients
    from miniagent.tools.html_upload import close_html_upload_http_clients

    await _shutdown_step("close_http_client", close_http_client())
    await _shutdown_step("close_html_upload_http_clients", close_html_upload_http_clients())
    await _shutdown_step("close_shared_httpx_clients", close_shared_httpx_clients())
    await _shutdown_step("close_browser_pool", close_browser_pool())
    if ctx.clawhub is not None:
        await _shutdown_step("ClawHub client close", ctx.clawhub.close())


async def _run_shutdown_housekeeping() -> None:
    """清理过期 trace 与自优化提案；外部状态缺失时安全跳过。"""
    from miniagent.core.self_opt.proposal_store import ProposalStore
    from miniagent.infrastructure.json_config import get_config
    from miniagent.infrastructure.trace_stats import cleanup_old_traces

    if get_config("trace.auto_cleanup", True):
        deleted = await asyncio.to_thread(
            cleanup_old_traces,
            get_config("trace.retention_days", 7),
        )
        if deleted > 0:
            _logger.info("shutdown: 清理过期trace文件 %d 个", deleted)
    proposals = await asyncio.to_thread(
        ProposalStore.cleanup_old_proposals,
        int(get_config("self_optimization.proposal_retention_days", 30)),
    )
    if proposals > 0:
        _logger.info("shutdown: 清理过期提案文件 %d 个", proposals)


def _begin_shutdown_trace() -> tuple[str, int, int]:
    """记录关闭阶段起点并返回 span 与墙钟/CPU 基线。"""
    from miniagent.infrastructure.tracing import emit_trace, new_trace_id

    span_id = new_trace_id("span")
    wall_start = time.monotonic_ns()
    cpu_start = time.process_time_ns()
    emit_trace({"type": "agent.phase_start", "phase": "shutdown", "span_id": span_id})
    return span_id, wall_start, cpu_start


async def _finish_shutdown_trace(span_id: str, wall_start: int, cpu_start: int) -> None:
    """写入关闭完成事件并最后排空 Trace writer。"""
    from miniagent.infrastructure.tracing import emit_trace, shutdown_trace_writer

    emit_trace(
        {
            "type": "agent.phase_end",
            "phase": "shutdown",
            "span_id": span_id,
            "duration_ms": (time.monotonic_ns() - wall_start) / 1_000_000,
            "cpu_ms": (time.process_time_ns() - cpu_start) / 1_000_000,
            "success": True,
        }
    )
    await _shutdown_thread_step("shutdown_trace_writer", shutdown_trace_writer)


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

    shutdown_span_id, shutdown_wall_start, shutdown_cpu_start = _begin_shutdown_trace()

    from miniagent.engine.session_continue import save_cli_session_state

    await _shutdown_thread_step("save_cli_session_state", save_cli_session_state, ctx, state)

    from miniagent.engine.session_lock import release_session_lock

    # 1) 先停止静态生产者，避免取消任务快照后 ticker / Feishu 又提交新工作。
    await _stop_runtime_work(ctx, abort_message_queues=abort_message_queues)
    await _close_protocol_and_process_resources()

    # 5a) 记忆运行时由 ApplicationContainer 独占；退出前统一持久化全部派生索引。
    await _shutdown_thread_step("memory runtime close", ctx.memory.close)

    # Close the active pool and pools retired by hot reload. Retired pools stay
    # alive until this point so in-flight turns using an old client can finish.
    await _close_network_resources(ctx)
    await _shutdown_step("housekeeping", _run_shutdown_housekeeping())

    if release_cli_session_lock:
        sid = (state.get("active_session_id") or "").strip()
        if sid:
            await _shutdown_thread_step("release_session_lock", release_session_lock, sid)

    if call_unregister:
        await _shutdown_thread_step("unregister_instance", unregister_instance)

    await _finish_shutdown_trace(shutdown_span_id, shutdown_wall_start, shutdown_cpu_start)

    # 6) 默认线程池：不再主动关闭。prompt_toolkit 的 in_terminal() 异步上下文退出时
    # 仍会通过 run_in_executor 使用默认线程池，此处提前关闭会导致
    # "Executor shutdown has been called"。由进程退出时自动清理即可。

    if reason:
        _logger.info("shutdown_runtime: done (%s)", reason)


__all__ = ["shutdown_runtime"]
