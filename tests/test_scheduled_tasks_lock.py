"""跨进程 scheduler/job 锁与 tasks.json 文件锁。"""

from __future__ import annotations

import json
import os
import threading
import time

from miniagent.assistant.scheduled_tasks.file_lock import tasks_json_lock
from miniagent.assistant.scheduled_tasks.lock import (
    release_job_lock,
    release_scheduler_lock,
    try_acquire_job_lock,
    try_acquire_scheduler_lock,
)
from miniagent.assistant.scheduled_tasks.models import ScheduledTask, ScheduleSpec, SessionSpec
from miniagent.assistant.scheduled_tasks.store import load_tasks, save_tasks, tasks_file_path


def test_scheduler_lock_acquire_release_roundtrip(state_dir: str) -> None:
    assert try_acquire_scheduler_lock() is True
    release_scheduler_lock()
    assert try_acquire_scheduler_lock() is True
    release_scheduler_lock()


def test_job_lock_per_task_id(state_dir: str) -> None:
    assert try_acquire_job_lock("task_a") is True
    assert try_acquire_job_lock("task_b") is True
    release_job_lock("task_a")
    release_job_lock("task_b")


def test_tasks_json_lock_blocks_concurrent_file_access(state_dir: str) -> None:
    """Windows 上 msvcrt 文件锁不可重入，避免在锁内再调 load_tasks。"""
    p = tasks_file_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"version": 2, "tasks": []}, f)

    def writer(suffix: str) -> None:
        with tasks_json_lock():
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            data["tasks"].append(
                {
                    "id": f"id_{suffix}",
                    "name": f"n_{suffix}",
                    "prompt": "p",
                    "schedule": {"kind": "interval", "interval_seconds": 60},
                    "session": {"mode": "primary"},
                }
            )
            time.sleep(0.02)
            with open(p, "w", encoding="utf-8") as f:
                json.dump(data, f)

    threads = [threading.Thread(target=writer, args=(str(i),)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    with open(p, encoding="utf-8") as f:
        n = len(json.load(f)["tasks"])
    assert n == 4


def test_save_tasks_load_tasks_roundtrip_uses_internal_lock(state_dir: str) -> None:
    """save_tasks/load_tasks 各自持 tasks_json_lock；勿在外层再包锁（Windows 不可重入）。"""
    t = ScheduledTask(
        id="lock_rt",
        name="lock_rt",
        prompt="p",
        schedule=ScheduleSpec(kind="interval", interval_seconds=60),
        session=SessionSpec(mode="primary"),
    )
    save_tasks([t])
    loaded = load_tasks()
    assert len(loaded) == 1
    assert loaded[0].id == "lock_rt"


def test_load_tasks_skips_corrupt_json(state_dir: str) -> None:
    p = tasks_file_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write("{not json")
    assert load_tasks() == []


def test_load_tasks_skips_invalid_task_entries(state_dir: str) -> None:
    p = tasks_file_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(
            {
                "schema_version": 2,
                "tasks": [
                    {
                        "id": "ok",
                        "name": "n",
                        "prompt": "p",
                        "schedule": {"kind": "interval", "interval_seconds": 60},
                        "session": {"mode": "primary"},
                    },
                    {"bad": True},
                ],
            },
            f,
        )
    loaded = load_tasks()
    assert len(loaded) == 1
    assert loaded[0].id == "ok"
