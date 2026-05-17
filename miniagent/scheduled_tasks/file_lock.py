"""``tasks.json`` 读写互斥：同进程线程锁 + 跨进程文件锁。"""

from __future__ import annotations

import contextlib
import os
import sys
import threading
from collections.abc import Iterator

_thread_lock = threading.RLock()


def _tasks_json_lock_path() -> str:
    root = os.environ.get("MINI_AGENT_STATE", os.path.join(os.getcwd(), "workspaces"))
    return os.path.join(root, "scheduled_tasks", "tasks.json.lock")


@contextlib.contextmanager
def tasks_json_lock() -> Iterator[None]:
    """在 load/save ``tasks.json`` 期间持有独占锁。"""
    lock_path = _tasks_json_lock_path()
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    with _thread_lock:
        with open(lock_path, "a+b") as lock_f:
            try:
                if sys.platform == "win32":
                    import msvcrt

                    lock_f.seek(0)
                    msvcrt.locking(lock_f.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl

                    fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
                yield
            finally:
                if sys.platform == "win32":
                    import msvcrt

                    try:
                        lock_f.seek(0)
                        msvcrt.locking(lock_f.fileno(), msvcrt.LK_UNLCK, 1)
                    except OSError:
                        pass
                else:
                    import fcntl

                    try:
                        fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)
                    except OSError:
                        pass
