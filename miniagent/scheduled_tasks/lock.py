"""跨进程互斥：``scheduler.lock``（tick）与 ``job_<id>.lock``（单任务执行）。

设计动机：多开 CLI 时避免重复触发同一 ``tasks.json``；PID 失效则下一实例可抢占。
与 ``dream_scheduler`` 的 ``dream.lock`` 思路一致，文件路径不同。"""

from __future__ import annotations

import os
import re

from miniagent.scheduled_tasks.store import tasks_dir

# 锁文件重试次数
LOCK_RETRY_COUNT = 3

_JOB_ID_SAFE = re.compile(r"[^a-zA-Z0-9_-]+")


def _job_lock_path(task_id: str) -> str:
    safe = _JOB_ID_SAFE.sub("_", (task_id or "").strip())[:120] or "job"
    return os.path.join(tasks_dir(), f"job_{safe}.lock")


def _try_acquire_lock_file(lock: str) -> bool:
    from miniagent.infrastructure.instance import is_process_running

    for _ in range(LOCK_RETRY_COUNT):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return True
        except FileExistsError:
            try:
                with open(lock, encoding="utf-8") as f:
                    pid = int(f.read().strip() or "0")
            except Exception:
                return False
            if pid and pid != os.getpid() and is_process_running(pid):
                return False
            try:
                os.unlink(lock)
            except OSError:
                return False
    return False


def _release_lock_file(lock: str) -> None:
    try:
        if os.path.isfile(lock):
            with open(lock, encoding="utf-8") as f:
                if f.read().strip() == str(os.getpid()):
                    os.unlink(lock)
    except OSError:
        pass


def try_acquire_job_lock(task_id: str) -> bool:
    """单条任务执行期独占；另一进程正在跑同一 ``task_id`` 时返回 False。"""
    return _try_acquire_lock_file(_job_lock_path(task_id))


def release_job_lock(task_id: str) -> None:
    """释放 ``try_acquire_job_lock`` 取得的任务锁。"""
    _release_lock_file(_job_lock_path(task_id))


def try_acquire_scheduler_lock() -> bool:
    """在 ``tasks_dir()/scheduler.lock`` 上创建独占文件；他进程存活则放弃。"""
    return _try_acquire_lock_file(os.path.join(tasks_dir(), "scheduler.lock"))


def release_scheduler_lock() -> None:
    """仅当锁内 PID 为当前进程时删除锁文件（忽略错误）。"""
    _release_lock_file(os.path.join(tasks_dir(), "scheduler.lock"))
