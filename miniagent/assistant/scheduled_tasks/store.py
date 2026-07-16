"""定时任务持久化：``tasks.json`` 的读写、下次触发时间计算与运行后重算。

路径根由 ``resolve_state_dir()`` 决定（见 ``miniagent.assistant.infrastructure.paths``）。"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timezone, tzinfo
from typing import Literal

from miniagent.agent.logging import get_logger
from miniagent.agent.timezone import process_timezone
from miniagent.assistant.infrastructure.atomic_json import atomic_dump_json
from miniagent.assistant.infrastructure.json_config import get_config
from miniagent.assistant.infrastructure.paths import resolve_state_dir as get_state_root
from miniagent.assistant.infrastructure.persistence import load_state_file
from miniagent.assistant.infrastructure.state_schemas import install_builtin_state_schemas
from miniagent.assistant.scheduled_tasks.file_lock import tasks_json_lock
from miniagent.assistant.scheduled_tasks.models import ScheduledTask, ScheduleSpec

_logger = get_logger(__name__)
_utc_timezone_hint_logged: set[str] = set()

_FILE_VERSION = 2
install_builtin_state_schemas()

TaskRunOutcome = Literal[
    "completed",
    "agent_error",
    "dispatch_failed",
    "skipped",
    "cancelled",
]


def dispatch_failure_backoff_seconds() -> int:
    """调度失败退避秒数，默认 60。"""
    sec = get_config("scheduled_tasks.dispatch_backoff", 60)
    return max(1, int(sec))


def tasks_dir() -> str:
    """``scheduled_tasks`` 目录路径（不存在则创建）。"""
    d = os.path.join(get_state_root(), "scheduled_tasks")
    os.makedirs(d, exist_ok=True)
    return d


def tasks_file_path() -> str:
    """``tasks.json`` 绝对路径。"""
    return os.path.join(tasks_dir(), "tasks.json")


def load_tasks() -> list[ScheduledTask]:
    """读取磁盘任务列表；文件缺失或损坏时返回空列表（不抛）。"""
    p = tasks_file_path()
    if not os.path.isfile(p):
        return []
    with tasks_json_lock():
        try:
            raw = load_state_file("scheduled_tasks", p)
        except (OSError, json.JSONDecodeError) as e:
            _logger.warning("读取任务文件失败: %s - %s", p, e)
            return []
        if not isinstance(raw, dict) or "tasks" not in raw:
            return []
        out: list[ScheduledTask] = []
        for item in raw.get("tasks") or []:
            if isinstance(item, dict):
                try:
                    out.append(ScheduledTask.from_json(item))
                except (KeyError, TypeError, ValueError) as e:
                    _logger.debug("解析任务条目失败: %s - %s", item.get("id", "unknown"), e)
                    continue
        return out


def save_tasks(tasks: list[ScheduledTask]) -> None:
    """原子写回 ``tasks.json``（唯一临时名 + ``os.replace``；Windows 上带短退避重试）。"""
    p = tasks_file_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    payload = {
        "schema_version": _FILE_VERSION,
        "tasks": [t.to_json() for t in tasks],
    }
    delays = (0.0, 0.02, 0.05, 0.1, 0.2, 0.35)
    last_err: OSError | None = None
    with tasks_json_lock():
        for attempt, delay in enumerate(delays):
            if delay > 0:
                time.sleep(delay)
            try:
                atomic_dump_json(p, payload, ensure_ascii=False, indent=2)
                return
            except OSError as e:
                last_err = e
                if attempt < len(delays) - 1:
                    continue
                raise last_err from e


async def save_tasks_async(tasks: list[ScheduledTask]) -> None:
    """异步保存任务列表（通过 to_thread 避免阻塞事件循环）。

    用于异步上下文（如 ticker）中保存任务，
    将 save_tasks 包装到独立线程执行，避免 time.sleep 阻塞主事件循环。

    注意：tasks_json_lock() 使用 threading.RLock + 文件锁（跨进程），
    无法改为 asyncio 锁，但整个 save 操作在线程中运行，不阻塞主循环。

    Args:
        tasks: 任务列表
    """
    await asyncio.to_thread(save_tasks, tasks)


def effective_task_timezone(task: ScheduledTask) -> str:
    """返回任务显式保存的 IANA 时区。"""
    return (task.schedule.timezone or "").strip() or process_timezone()


def _parse_once_utc_epoch(
    spec: ScheduleSpec,
    now_ts: float,
    *,
    tz_name: str | None = None,
) -> float | None:
    """将 once_at_iso 转为 UTC epoch；无法解析则 None。"""
    raw = (spec.once_at_iso or "").strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            tz: tzinfo
            from zoneinfo import ZoneInfo

            try:
                tz = ZoneInfo((tz_name or spec.timezone or "UTC").strip() or "UTC")
            except Exception:
                tz = timezone.utc
            dt = dt.replace(tzinfo=tz)
        return dt.timestamp()
    except (ValueError, OSError):
        return None


def _cron_next(task: ScheduledTask, after_ts: float) -> float | None:
    """计算 cron 表达式的下一次触发时间戳；表达式无效时返回 None。"""
    from miniagent.assistant.scheduled_tasks.cron import cron_next_run_epoch

    expr = (task.schedule.cron_expr or "").strip()
    if not expr:
        return None
    try:
        return cron_next_run_epoch(expr, effective_task_timezone(task), after_ts)
    except ValueError:
        return None


def _cron_validate_error(task: ScheduledTask) -> str | None:
    """校验 cron 任务表达式；无效时返回错误摘要，有效返回 None。"""
    if task.schedule.kind != "cron":
        return None
    expr = (task.schedule.cron_expr or "").strip()
    if not expr:
        return "cron: empty expression"
    from miniagent.assistant.scheduled_tasks.cron import validate_cron_expr

    try:
        validate_cron_expr(expr)
    except ValueError as e:
        return f"invalid cron: {e}"
    return None


def repair_invalid_schedules(tasks: list[ScheduledTask], now_ts: float | None = None) -> bool:
    """补齐合法任务的 next_run_at；非法 cron 写入 last_error 并清空 next_run_at。"""
    env_tz = process_timezone()
    now = now_ts if now_ts is not None else time.time()
    changed = False
    for t in tasks:
        if (
            t.enabled
            and (t.schedule.timezone or "").strip() == "UTC"
            and env_tz != "UTC"
            and t.id not in _utc_timezone_hint_logged
        ):
            _utc_timezone_hint_logged.add(t.id)
            _logger.info(
                "定时任务 %s 时区为 UTC，与当前环境默认 %s 不一致；"
                "可用 .schedule update %s … --tz %s 修正",
                t.id,
                env_tz,
                t.id,
                env_tz,
            )
        if not t.enabled:
            continue
        err = _cron_validate_error(t)
        if err:
            if t.last_error != err:
                t.last_error = err
                changed = True
            if t.next_run_at is not None:
                t.next_run_at = None
                changed = True
            continue
        if t.next_run_at is None:
            n = compute_initial_next_run(t, now)
            if n is not None:
                t.next_run_at = n
                changed = True
    return changed


def apply_dispatch_failure_backoff(task: ScheduledTask, now_ts: float | None = None) -> None:
    """dispatch/包装失败时推迟下次触发，避免秒级重试风暴。"""
    now = now_ts if now_ts is not None else time.time()
    task.next_run_at = now + dispatch_failure_backoff_seconds()


def finalize_task_after_run(
    task: ScheduledTask,
    *,
    outcome: TaskRunOutcome,
    agent_error: str | None = None,
    now_ts: float | None = None,
) -> None:
    """按运行结果更新任务状态（跳过/取消不改 next_run_at，失败退避，成功重算）。"""
    if outcome in ("skipped", "cancelled"):
        return
    now = now_ts if now_ts is not None else time.time()
    if outcome == "dispatch_failed":
        task.last_error = task.last_error or "dispatch/wrap failure (see logs)"
        apply_dispatch_failure_backoff(task, now)
        return
    task.last_run_at = now
    task.run_count = int(task.run_count or 0) + 1
    task.last_error = agent_error if outcome == "agent_error" else None
    recompute_next_after_run(task, now)


def format_next_run_display(task: ScheduledTask, *, now_ts: float | None = None) -> str:
    """list/show 用的人类可读下次触发说明。"""
    nxt = task.next_run_at
    if nxt is None:
        err = (task.last_error or "").strip()
        if err:
            return f"err ({err[:80]})"
        return "-"
    now = now_ts if now_ts is not None else time.time()
    delta = float(nxt) - now
    if delta <= 0:
        when = "due"
    elif delta < 90:
        when = f"in {int(delta)}s"
    elif delta < 7200:
        when = f"in {int(delta // 60)}m"
    else:
        when = f"in {int(delta // 3600)}h"
    try:
        from miniagent.agent.timezone import format_process_local

        eff = effective_task_timezone(task)
        local = format_process_local(float(nxt), tz_name=eff)
        return f"{local} ({when}) tz={eff}"
    except Exception:
        return f"{nxt:.0f} ({when})"


def compute_initial_next_run(task: ScheduledTask, now_ts: float | None = None) -> float | None:
    """新建或加载后补齐 next_run_at。

    ``once`` 模式：若 ``once_at_iso`` 已早于 ``now_ts``，仍返回该时刻戳，
    调度器会在下一次 tick 立即触发（执行后由 ``recompute_next_after_run`` 禁用任务）。
    """
    now = now_ts if now_ts is not None else time.time()
    spec = task.schedule
    if spec.kind == "interval":
        sec = int(spec.interval_seconds or 0)
        if sec <= 0:
            return None
        return now + sec
    if spec.kind == "once":
        return _parse_once_utc_epoch(spec, now, tz_name=effective_task_timezone(task))
    if spec.kind == "cron":
        return _cron_next(task, now)
    return None


def recompute_next_after_run(task: ScheduledTask, now_ts: float | None = None) -> None:
    """执行一轮后更新 next_run_at；once 任务则禁用。"""
    now = now_ts if now_ts is not None else time.time()
    spec = task.schedule
    if spec.kind == "once":
        task.next_run_at = None
        task.enabled = False
        return
    if spec.kind == "cron":
        after = task.last_run_at if task.last_run_at is not None else now
        task.next_run_at = _cron_next(task, after)
        return
    sec = int(spec.interval_seconds or 0)
    if sec > 0:
        task.next_run_at = now + sec
    else:
        task.next_run_at = None
