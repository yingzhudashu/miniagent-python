"""进程内 asyncio 调度循环：周期性 ``tick_once``、加锁、将到期任务经 ``message_queue`` 投递执行。

与 ``engine.main`` 中启动的 ``start_scheduled_tasks_ticker`` 配套；环境变量 ``MINIAGENT_DISABLE_SCHEDULED_TASKS`` 可关闭。

并发语义：同一进程内单 ticker 循环；跨进程通过 ``scheduler.lock``（见 ``lock``）避免重复 tick。"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any

from miniagent.engine.cli_state import CliLoopState
from miniagent.infrastructure.logger import get_logger
from miniagent.runtime.context import RuntimeContext
from miniagent.scheduled_tasks.lock import release_scheduler_lock, try_acquire_scheduler_lock
from miniagent.scheduled_tasks.models import ScheduledTask
from miniagent.scheduled_tasks.runner import build_run_scheduled_job_coro
from miniagent.scheduled_tasks.store import (
    compute_initial_next_run,
    load_tasks,
    recompute_next_after_run,
    save_tasks,
)

_logger = get_logger(__name__)

_inflight: set[str] = set()
_MAX_DUE_PER_TICK = 5


def _sleep_seconds_until(tasks: list[ScheduledTask]) -> float:
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


def _sync_missing_next_runs(tasks: list[ScheduledTask]) -> bool:
    """为已启用且尚无 next_run_at 的任务补齐时间；若有变更返回 True。"""
    changed = False
    now = time.time()
    for t in tasks:
        if not t.enabled or t.next_run_at is not None:
            continue
        n = compute_initial_next_run(t, now)
        if n is not None:
            t.next_run_at = n
            changed = True
    return changed


async def tick_once(
    ctx: RuntimeContext,
    state: CliLoopState,
    skill_toolboxes: list[Any],
    skill_prompts: list[Any],
) -> None:
    """单次调度：加锁、选出到期任务、经 ``message_queue`` 异步投递 ``build_run_scheduled_job_coro``。"""
    if os.environ.get("MINIAGENT_DISABLE_SCHEDULED_TASKS", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return

    if not try_acquire_scheduler_lock():
        return
    try:
        tasks = load_tasks()
        if _sync_missing_next_runs(tasks):
            save_tasks(tasks)

        now = time.time()
        due: list[ScheduledTask] = []
        for t in tasks:
            if not t.enabled or t.id in _inflight:
                continue
            if t.next_run_at is not None and t.next_run_at <= now:
                due.append(t)
        due.sort(key=lambda x: float(x.next_run_at or 0))
        due = due[:_MAX_DUE_PER_TICK]

        mq = ctx.message_queue
        for t in due:
            job_id = t.id
            _inflight.add(job_id)

            async def _one_job(task_id: str = job_id) -> None:
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
                    err = err_holder[0]

                    tlist2 = load_tasks()
                    task2 = next((x for x in tlist2 if x.id == task_id), None)
                    if task2:
                        task2.last_run_at = time.time()
                        task2.run_count = int(task2.run_count or 0) + 1
                        if err:
                            task2.last_error = err
                        else:
                            task2.last_error = None
                        recompute_next_after_run(task2)
                        save_tasks(tlist2)
                except Exception:
                    _logger.exception("定时任务包装执行失败: %s", task_id)
                    try:
                        tlist3 = load_tasks()
                        task3 = next((x for x in tlist3 if x.id == task_id), None)
                        if task3:
                            task3.last_error = "dispatch/wrap failure (see logs)"
                            save_tasks(tlist3)
                    except Exception:
                        pass
                finally:
                    _inflight.discard(task_id)

            asyncio.create_task(_one_job())
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
        await scheduled_tasks_loop(ctx, state, skill_toolboxes, skill_prompts, stop_event)

    task = asyncio.create_task(_runner(), name="miniagent_scheduled_tasks")
    ctx.scheduled_tasks_ticker = task
    return task
