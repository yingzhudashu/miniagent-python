"""进程内 asyncio 调度循环：周期性 ``tick_once``、加锁、将到期任务经 ``message_queue`` 投递执行。

与 ``engine.main`` 中启动的 ``start_scheduled_tasks_ticker`` 配套；配置 ``scheduled_tasks.disabled`` 可关闭。

并发语义：同一进程内单 ticker 循环；跨进程通过 ``scheduler.lock``（tick）与 ``job_<id>.lock``（执行）避免重复触发。"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from miniagent.agent.logging import get_logger
from miniagent.assistant.application.messaging.inbound import InboundTurnCoordinator
from miniagent.assistant.bootstrap.application import ApplicationContainer
from miniagent.assistant.contracts.messages import InboundMessage
from miniagent.assistant.engine.cli_state import CliLoopState
from miniagent.assistant.infrastructure.json_config import get_config
from miniagent.assistant.scheduled_tasks.lock import (
    release_job_lock,
    release_scheduler_lock,
    try_acquire_job_lock,
    try_acquire_scheduler_lock,
)
from miniagent.assistant.scheduled_tasks.models import ScheduledTask
from miniagent.assistant.scheduled_tasks.runner import build_scheduled_job
from miniagent.assistant.scheduled_tasks.store import (
    TaskRunOutcome,
    finalize_task_after_run,
    load_tasks,
    repair_invalid_schedules,
    save_tasks_async,
)

_logger = get_logger(__name__)

# 同进程内已投递、尚未写完状态的 task id；与 job_<id>.lock 互补防重复触发
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


def _select_due_tasks(tasks: list[ScheduledTask], now: float) -> list[ScheduledTask]:
    """获取任务锁并返回本 tick 可投递的有界任务列表。"""
    due: list[ScheduledTask] = []
    for task in tasks:
        if not task.enabled or task.id in _inflight:
            continue
        if task.next_run_at is None or task.next_run_at > now:
            continue
        if try_acquire_job_lock(task.id):
            due.append(task)
    due.sort(key=lambda item: float(item.next_run_at or 0))
    selected = due[:_MAX_DUE_PER_TICK]
    for task in due[_MAX_DUE_PER_TICK:]:
        release_job_lock(task.id)
    return selected


async def _finalize_scheduled_job(
    task_id: str,
    *,
    outcome: TaskRunOutcome,
    agent_error: str | None,
) -> None:
    """尽力写回任务结果，并无条件释放进程内标记和跨进程锁。"""
    try:
        tasks = load_tasks()
        task = next((item for item in tasks if item.id == task_id), None)
        if task:
            finalize_task_after_run(task, outcome=outcome, agent_error=agent_error)
            await save_tasks_async(tasks)
    except Exception:
        _logger.exception("定时任务写回状态失败: %s", task_id)
    finally:
        _inflight.discard(task_id)
        release_job_lock(task_id)


async def _run_scheduled_job(
    task_id: str,
    *,
    ctx: ApplicationContainer,
    state: CliLoopState,
    inbound_turns: InboundTurnCoordinator,
    skill_toolboxes: list[Any],
    skill_prompts: list[Any],
) -> None:
    """执行单个已锁定任务；取消继续传播，最终状态始终写回。"""
    outcome: TaskRunOutcome = "skipped"
    agent_error: str | None = None
    try:
        tasks = load_tasks()
        task = next((item for item in tasks if item.id == task_id), None)
        if task is None or not task.enabled:
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
            outcome=outcome,
            agent_error=agent_error,
        )


async def tick_once(
    ctx: ApplicationContainer,
    state: CliLoopState,
    skill_toolboxes: list[Any] | None = None,
    skill_prompts: list[Any] | None = None,
) -> None:
    """单次调度：加锁、选出到期任务、经 message_queue 异步投递执行协程。

    执行流程：
    1. 获取调度锁（防止多进程并发调度）
    2. 加载任务列表，修复无效 cron
    3. 选出到期任务（next_run_at <= now）
    4. 尝试获取任务级锁
    5. 构建 job 协程并投递到 message_queue
    6. 等待执行完成，更新任务状态

    Args:
        ctx: 运行时上下文（含 message_queue、engine 等）
        state: CLI 循环状态（含技能快照）
        skill_toolboxes: 技能工具箱列表（可选，优先从 state 读取）
        skill_prompts: 技能提示列表（可选，优先从 state 读取）

    Note:
        - 调度锁是进程级的（tasks.json.lock）
        - 任务锁是 job 级的（job_<id>.lock）
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

    if not try_acquire_scheduler_lock():
        return
    try:
        tasks = load_tasks()
        if repair_invalid_schedules(tasks):
            await save_tasks_async(tasks)

        due = _select_due_tasks(tasks, time.time())

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
                    job_id,
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
    finally:
        release_scheduler_lock()


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
