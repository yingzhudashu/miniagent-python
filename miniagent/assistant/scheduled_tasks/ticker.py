"""进程内 asyncio 调度循环：原子 claim 到期任务并经 ``message_queue`` 投递执行。

与 ``engine.main`` 中启动的 ``start_scheduled_tasks_ticker`` 配套；配置 ``scheduled_tasks.disabled`` 可关闭。

并发语义：同一进程内单 ticker 循环；跨进程由 SQLite 写事务和 task claim 保证唯一执行。"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

from miniagent.agent.logging import get_logger
from miniagent.assistant.application.messaging.inbound import InboundTurnCoordinator
from miniagent.assistant.bootstrap.application import ApplicationContainer
from miniagent.assistant.engine.cli_state import CliLoopState
from miniagent.assistant.infrastructure.json_config import get_config
from miniagent.assistant.scheduled_tasks.models import ScheduledTask
from miniagent.assistant.scheduled_tasks.runner import build_scheduled_job
from miniagent.assistant.scheduled_tasks.store import (
    TaskRunOutcome,
    claim_due_tasks,
    finalize_claimed_task,
    load_tasks,
    repair_invalid_schedules,
    save_tasks_async,
)
from miniagent.ui.messages import InboundMessage

_logger = get_logger(__name__)

# 同进程内已投递、尚未写完状态的 task id；持久化唯一性由 claim 保证。
_inflight: set[str] = set()
_MAX_DUE_PER_TICK = 5


def _sleep_seconds_until(tasks: list[ScheduledTask]) -> float:
    """根据已启用任务的 ``next_run_at`` 计算下一次唤醒前的睡眠秒数（有界 0.5～60s）。"""
    now = time.time()
    candidates = [float(t.next_run_at) for t in tasks if t.enabled and t.next_run_at is not None]
    if not candidates:
        return 60.0
    nxt = min(candidates)
    return max(0.5, min(60.0, nxt - now))


async def _finalize_scheduled_job(
    task_id: str,
    owner: str,
    *,
    outcome: TaskRunOutcome,
    agent_error: str | None,
) -> None:
    """Write one owned result and unconditionally release the in-process marker."""
    try:
        await asyncio.to_thread(
            finalize_claimed_task,
            task_id,
            owner,
            outcome=outcome,
            agent_error=agent_error,
        )
    except Exception:
        _logger.exception("定时任务写回状态失败: %s", task_id)
    finally:
        _inflight.discard(task_id)


async def _run_scheduled_job(
    task: ScheduledTask,
    owner: str,
    *,
    ctx: ApplicationContainer,
    state: CliLoopState,
    inbound_turns: InboundTurnCoordinator,
    skill_toolboxes: list[Any],
    skill_prompts: list[Any],
) -> None:
    """Execute one claimed task; cancellation propagates and releases its claim."""
    task_id = task.id
    outcome: TaskRunOutcome = "skipped"
    agent_error: str | None = None
    try:
        if not task.enabled:
            return
        job = build_scheduled_job(ctx, state, task, skill_toolboxes, skill_prompts)
        errors: list[str | None] = [None]

        async def handle(message: InboundMessage) -> None:
            errors[0] = await job.run(message)

        await inbound_turns.submit(job.message, handle, wait=True)
        agent_error = errors[0]
        outcome = "agent_error" if agent_error else "completed"
    except asyncio.CancelledError:
        outcome = "cancelled"
        raise
    except Exception:
        _logger.exception("定时任务包装执行失败: %s", task_id)
        outcome = "dispatch_failed"
    finally:
        await _finalize_scheduled_job(
            task_id,
            owner,
            outcome=outcome,
            agent_error=agent_error,
        )


async def tick_once(
    ctx: ApplicationContainer,
    state: CliLoopState,
    skill_toolboxes: list[Any] | None = None,
    skill_prompts: list[Any] | None = None,
) -> None:
    """单次调度：原子 claim 到期任务并经 message_queue 异步投递执行协程。

    执行流程：
    1. 加载任务列表并修复无效 cron
    2. 事务内 claim 到期任务
    3. 构建 job 协程并投递到 message_queue
    4. 按 owner 原子写回执行状态并释放 claim

    Args:
        ctx: 运行时上下文（含 message_queue、engine 等）
        state: CLI 循环状态（含技能快照）
        skill_toolboxes: 技能工具箱列表（可选，优先从 state 读取）
        skill_prompts: 技能提示列表（可选，优先从 state 读取）

    Note:
        - 单次最多处理 _MAX_DUE_PER_TICK 个任务
        - 执行完成后自动重算 next_run_at
    """
    from miniagent.assistant.skills.snapshots import (
        get_skill_prompts_from_state,
        get_skill_toolboxes_from_state,
    )

    skill_toolboxes = get_skill_toolboxes_from_state(state) or skill_toolboxes or []
    skill_prompts = get_skill_prompts_from_state(state) or skill_prompts or []
    if get_config("scheduled_tasks.disabled", False):
        return

    tasks = load_tasks()
    if repair_invalid_schedules(tasks):
        await save_tasks_async(tasks)

    owner = f"scheduler:{os.getpid()}:{id(ctx)}"
    due = await asyncio.to_thread(
        claim_due_tasks,
        owner,
        now_ts=time.time(),
        limit=_MAX_DUE_PER_TICK,
    )

    mq = ctx.message_queue
    inbound_turns = InboundTurnCoordinator(
        mq,
        queue_key=lambda message: str(message.metadata.get("queue_key") or ""),
    )
    for task in due:
        job_id = task.id
        _inflight.add(job_id)
        jt = asyncio.create_task(
            _run_scheduled_job(
                task,
                owner,
                ctx=ctx,
                state=state,
                inbound_turns=inbound_turns,
                skill_toolboxes=skill_toolboxes,
                skill_prompts=skill_prompts,
            )
        )
        reg = getattr(ctx, "register_shutdown_tracked_task", None)
        if callable(reg):
            reg(jt)


async def scheduled_tasks_loop(
    ctx: ApplicationContainer,
    state: CliLoopState,
    skill_toolboxes: list[Any],
    skill_prompts: list[Any],
    stop_event: asyncio.Event,
) -> None:
    """根据下一触发时间睡眠并反复调用 ``tick_once``；由 ``stop_event`` 终止。"""
    from miniagent.assistant.scheduled_tasks.trace_cleanup import TraceHousekeeping

    trace_housekeeping = TraceHousekeeping()
    while not stop_event.is_set():
        tasks = load_tasks()
        delay = _sleep_seconds_until(tasks)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=delay)
            if stop_event.is_set():
                break
        except asyncio.TimeoutError:
            _logger.debug("等待超时，继续检查")
        if stop_event.is_set():
            break
        try:
            await tick_once(ctx, state, skill_toolboxes, skill_prompts)
        except Exception:
            _logger.exception("scheduled_tasks tick 异常")
        try:
            trace_housekeeping.maybe_cleanup()
            trace_housekeeping.maybe_report()
        except Exception:
            _logger.debug("trace housekeeping tick skipped", exc_info=True)


def start_scheduled_tasks_ticker(
    ctx: ApplicationContainer,
    state: CliLoopState,
    skill_toolboxes: list[Any],
    skill_prompts: list[Any],
    stop_event: asyncio.Event,
) -> asyncio.Task[Any]:
    """Create the scheduler task using a lifecycle-owned stop event."""

    async def _runner() -> None:
        """后台入口：运行 ``scheduled_tasks_loop`` 直至 ``stop_event``。"""
        await scheduled_tasks_loop(ctx, state, skill_toolboxes, skill_prompts, stop_event)

    task = asyncio.create_task(_runner(), name="miniagent_scheduled_tasks")
    return task
