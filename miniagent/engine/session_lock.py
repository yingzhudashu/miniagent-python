"""Engine — 会话级锁管理

拆分自 unified.py。每个会话一个 .lock 文件，位于 workspaces/<session_id>/.lock。

特性：
- 跨实例锁检测（PID 存活检查）
- 过期锁自动清理（进程死亡后）
- 幂等锁定（同一进程重复锁定成功）

与多实例 PID 语义互补说明见 ``docs/INSTANCE_REGISTRY.md``。
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys

from miniagent.session.manager import _get_workspaces_dir


def _safe_session_id(session_id: str) -> str:
    """将非法路径字符替换为安全字符，与 ``DefaultSessionManager._make_safe_id`` 一致。"""
    return re.sub(r'[<>:"/\\|?*]', "_", session_id)


def _get_lock_path(session_id: str) -> str:
    """获取会话锁文件路径。"""
    safe = _safe_session_id(session_id)
    return os.path.join(_get_workspaces_dir(), safe, ".lock")


def _is_process_running(pid: int) -> bool:
    """检测 PID 是否存活。

    Windows: 通过 tasklist 查询
    POSIX: 通过 os.kill(pid, 0) 查询
    """
    try:
        if sys.platform == "win32":
            output = subprocess.check_output(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                timeout=5,
                text=True,
            )
            return f'"{pid}"' in output
        else:
            os.kill(pid, 0)
            return True
    except Exception:
        return False


async def _is_process_running_async(pid: int) -> bool:
    """异步检测 PID 是否存活（不阻塞事件循环）。

    用于异步上下文中的锁检测，避免 subprocess.check_output 阻塞。

    Args:
        pid: 进程 ID

    Returns:
        进程是否仍在运行
    """
    try:
        if sys.platform == "win32":
            proc = await asyncio.create_subprocess_exec(
                "tasklist",
                "/FI",
                f"PID eq {pid}",
                "/NH",
                "/FO",
                "CSV",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            return f'"{pid}"' in stdout.decode("utf-8", errors="replace")
        else:
            os.kill(pid, 0)
            return True
    except Exception:
        return False


def try_lock_session(session_id: str) -> tuple[bool, str]:
    """尝试获取会话锁。

    Returns:
        (success, reason) — success=True 表示锁获取成功
    """
    lock_path = _get_lock_path(session_id)
    my_pid = os.getpid()
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)

    if os.path.exists(lock_path):
        try:
            with open(lock_path) as f:
                locked_pid = int(f.read().strip())
            if locked_pid == my_pid:
                return True, ""  # 我自己锁的，幂等
            if _is_process_running(locked_pid):
                return False, f"被其他实例占用 (PID={locked_pid})"
            try:
                os.unlink(lock_path)
            except OSError:
                pass
        except (ValueError, OSError):
            try:
                os.unlink(lock_path)
            except OSError:
                pass

    with open(lock_path, "w") as f:
        f.write(str(my_pid))
    return True, ""


def release_session_lock(session_id: str) -> None:
    """释放会话锁（仅释放当前进程的锁）。"""
    lock_path = _get_lock_path(session_id)
    try:
        if os.path.exists(lock_path):
            with open(lock_path) as f:
                locked_pid = int(f.read().strip())
            if locked_pid == os.getpid():
                os.unlink(lock_path)
    except Exception:
        pass


def is_session_locked(session_id: str) -> int | None:
    """检查会话是否被其他实例锁定。

    Returns:
        占用者 PID，或 None 表示未锁定
    """
    lock_path = _get_lock_path(session_id)
    if not os.path.exists(lock_path):
        return None
    try:
        with open(lock_path) as f:
            locked_pid = int(f.read().strip())
        if locked_pid == os.getpid():
            return None
        if _is_process_running(locked_pid):
            return locked_pid
    except Exception:
        pass
    return None


__all__ = ["try_lock_session", "release_session_lock", "is_session_locked"]
