"""定时任务持久化：``tasks.json`` 的读写、下次触发时间计算与运行后重算。

路径根由 ``MINI_AGENT_STATE`` 或当前工作目录下 ``workspaces`` 决定，与 README/ENGINEERING 描述一致。"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

from miniagent.scheduled_tasks.models import ScheduledTask, ScheduleSpec

_FILE_VERSION = 1


def _state_root() -> str:
    """与引擎/记忆共用的状态根（``MINI_AGENT_STATE`` 或 ``<cwd>/workspaces``）。"""
    return os.environ.get("MINI_AGENT_STATE", os.path.join(os.getcwd(), "workspaces"))


def tasks_dir() -> str:
    """``scheduled_tasks`` 目录路径（不存在则创建）。"""
    d = os.path.join(_state_root(), "scheduled_tasks")
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
    try:
        with open(p, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, dict) or "tasks" not in raw:
        return []
    out: list[ScheduledTask] = []
    for item in raw.get("tasks") or []:
        if isinstance(item, dict):
            try:
                out.append(ScheduledTask.from_json(item))
            except (KeyError, TypeError, ValueError):
                continue
    return out


def save_tasks(tasks: list[ScheduledTask]) -> None:
    """原子写回 ``tasks.json``（先写临时文件再 ``os.replace``）。"""
    p = tasks_file_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    payload = {
        "version": _FILE_VERSION,
        "tasks": [t.to_json() for t in tasks],
    }
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def _parse_once_utc_epoch(spec: ScheduleSpec, now_ts: float) -> float | None:
    """将 once_at_iso 转为 UTC epoch；无法解析则 None。"""
    raw = (spec.once_at_iso or "").strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            from zoneinfo import ZoneInfo

            try:
                tz = ZoneInfo(spec.timezone or "UTC")
            except Exception:
                tz = timezone.utc
            dt = dt.replace(tzinfo=tz)
        return dt.timestamp()
    except (ValueError, OSError):
        return None


def compute_initial_next_run(task: ScheduledTask, now_ts: float | None = None) -> float | None:
    """新建或加载后补齐 next_run_at。"""
    now = now_ts if now_ts is not None else time.time()
    spec = task.schedule
    if spec.kind == "interval":
        sec = int(spec.interval_seconds or 0)
        if sec <= 0:
            return None
        return now + sec
    if spec.kind == "once":
        t = _parse_once_utc_epoch(spec, now)
        if t is None:
            return None
        return t
    return None


def recompute_next_after_run(task: ScheduledTask, now_ts: float | None = None) -> None:
    """成功执行一轮后更新 next_run_at；once 任务则禁用。"""
    now = now_ts if now_ts is not None else time.time()
    spec = task.schedule
    if spec.kind == "once":
        task.next_run_at = None
        task.enabled = False
        return
    sec = int(spec.interval_seconds or 0)
    if sec > 0:
        task.next_run_at = now + sec
    else:
        task.next_run_at = None
