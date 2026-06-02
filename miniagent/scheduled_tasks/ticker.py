"""进程内 asyncio 调度循环：周期性 ``tick_once``、加锁、将到期任务经 ``message_queue`` 投递执行。

与 ``engine.main`` 中启动的 ``start_scheduled_tasks_ticker`` 配套；配置 ``scheduled_tasks.disabled`` 可关闭。

并发语义：同一进程内单 ticker 循环；跨进程通过 ``scheduler.lock``（tick）与 ``job_<id>.lock``（执行）避免重复触发。"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from miniagent.engine.cli_state import CliLoopState
from miniagent.infrastructure.json_config import get_config
from miniagent.infrastructure.logger import get_logger
from miniagent.runtime.context import RuntimeContext
from miniagent.scheduled_tasks.lock import (
    release_job_lock,
    release_scheduler_lock,
    try_acquire_job_lock,
    try_acquire_scheduler_lock,
)
from miniagent.scheduled_tasks.models import ScheduledTask
from miniagent.scheduled_tasks.runner import build_run_scheduled_job_coro
from miniagent.scheduled_tasks.store import (
    TaskRunOutcome,
    finalize_task_after_run,
    load_tasks,
    repair_invalid_schedules,
    save_tasks_async,
)

_logger = get_logger(__name__)

_inflight: set[str] = set()
_MAX_DUE_PER_TICK = 5


def _sleep_seconds_until(tasks: list[ScheduledTask]) -> float:
    """根据已启用任务的 ``next_run_at`` 计算下一次唤醒前的睡眠秒数（有界 0.5～60s）。"""
    now = time.time()
    candidates: list[float] = []
    for t in tasks:
        if not t.enabled:
            continue
        n = t.next_run_at
        if n is not None:
            candidates.append(float(n))
    if not candidates:
        return 60.0
    nxt = min(candidates)
    return max(0.5, min(60.0, nxt - now))


async def tick_once(
    ctx: RuntimeContext,
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
    from miniagent.skills.snapshots import (
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

        now = time.time()
        due: list[ScheduledTask] = []
        for t in tasks:
            if not t.enabled or t.id in _inflight:
                continue
            if t.next_run_at is not None and t.next_run_at <= now:
                if not try_acquire_job_lock(t.id):
                    continue
                due.append(t)
        due.sort(key=lambda x: float(x.next_run_at or 0))
        due = due[:_MAX_DUE_PER_TICK]

        mq = ctx.message_queue
        for t in due:
            job_id = t.id
            _inflight.add(job_id)

            async def _one_job(task_id: str = job_id) -> None:
                outcome: TaskRunOutcome = "skipped"
                agent_error: str | None = None
                try:
                    tlist = load_tasks()
                    task = next((x for x in tlist if x.id == task_id), None)
                    if task is None or not task.enabled:
                        return
                    coro, mq_chat = build_run_scheduled_job_coro(
                        ctx,
                        state,
                        task,
                        skill_toolboxes,
                        skill_prompts,
                    )
                    err_holder: list[str | None] = [None]

                    async def _wrap() -> None:
                        err_holder[0] = await coro

                    if mq_chat == mq.CLI_CHAT_ID:
                        await mq.dispatch_cli_wait(_wrap())
                    else:
                        await mq.dispatch_wait(mq_chat, _wrap())
                    agent_error = err_holder[0]
                    outcome = "agent_error" if agent_error else "completed"
                except asyncio.CancelledError:
                    outcome = "cancelled"
                    raise
                except Exception:
                    _logger.exception("定时任务包装执行失败: %s", task_id)
                    outcome = "dispatch_failed"
                finally:
                    try:
                        tlist2 = load_tasks()
                        task2 = next((x for x in tlist2 if x.id == task_id), None)
                        if task2:
                            finalize_task_after_run(
                                task2,
                                outcome=outcome,
                                agent_error=agent_error,
                            )
                            await save_tasks_async(tlist2)
                    except Exception:
                        _logger.exception("定时任务写回状态失败: %s", task_id)
                    _inflight.discard(task_id)
                    release_job_lock(task_id)

            jt = asyncio.create_task(_one_job())
            reg = getattr(ctx, "register_shutdown_tracked_task", None)
            if callable(reg):
                reg(jt)
    finally:
        release_scheduler_lock()


async def scheduled_tasks_loop(
    ctx: RuntimeContext,
    state: CliLoopState,
    skill_toolboxes: list[Any],
    skill_prompts: list[Any],
    stop_event: asyncio.Event,
) -> None:
    """根据下一触发时间睡眠并反复调用 ``tick_once``；由 ``stop_event`` 终止。"""
    while not stop_event.is_set():
        tasks = load_tasks()
        delay = _sleep_seconds_until(tasks)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=delay)
            if stop_event.is_set():
                break
        except asyncio.TimeoutError:
            pass
        if stop_event.is_set():
            break
        try:
            await tick_once(ctx, state, skill_toolboxes, skill_prompts)
        except Exception:
            _logger.exception("scheduled_tasks tick 异常")


def start_scheduled_tasks_ticker(
    ctx: RuntimeContext,
    state: CliLoopState,
    skill_toolboxes: list[Any],
    skill_prompts: list[Any],
) -> asyncio.Task[Any]:
    """创建后台 Task 并写入 ``ctx.scheduled_tasks_*``，供进程退出时 cancel。"""
    stop_event = asyncio.Event()
    ctx.scheduled_tasks_stop_event = stop_event

    async def _runner() -> None:
        """后台入口：运行 ``scheduled_tasks_loop`` 直至 ``stop_event``。"""
        await scheduled_tasks_loop(ctx, state, skill_toolboxes, skill_prompts, stop_event)

    task = asyncio.create_task(_runner(), name="miniagent_scheduled_tasks")
    ctx.scheduled_tasks_ticker = task
    return task
