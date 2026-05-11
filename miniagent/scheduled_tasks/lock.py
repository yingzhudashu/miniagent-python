"""跨进程互斥：仅一个实例执行调度 tick（与 dream.lock 策略一致）。"""

from __future__ import annotations

import os

from miniagent.scheduled_tasks.store import tasks_dir


def try_acquire_scheduler_lock() -> bool:
    """在 ``tasks_dir()/scheduler.lock`` 上创建独占文件；他进程存活则放弃。"""
    from miniagent.infrastructure.instance import _is_process_running

    lock = os.path.join(tasks_dir(), "scheduler.lock")
    for _ in range(3):
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
            if pid and pid != os.getpid() and _is_process_running(pid):
                return False
            try:
                os.unlink(lock)
            except OSError:
                return False
    return False


def release_scheduler_lock() -> None:
    """仅当锁内 PID 为当前进程时删除锁文件（忽略错误）。"""
    lock = os.path.join(tasks_dir(), "scheduler.lock")
    try:
        if os.path.isfile(lock):
            with open(lock, encoding="utf-8") as f:
                if f.read().strip() == str(os.getpid()):
                    os.unlink(lock)
    except OSError:
        pass
