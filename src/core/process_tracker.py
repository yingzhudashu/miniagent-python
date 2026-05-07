"""子进程生命周期追踪器

防止主进程退出时遗留孤儿进程。

用法：
    from src.core.process_tracker import create_tracked_subprocess, cleanup_all_processes

    proc = await create_tracked_subprocess("long_running_cmd")
    # ... 主进程退出时自动调用 cleanup_all_processes()
"""

from __future__ import annotations

import asyncio
import atexit
import os
import subprocess
import sys

from src.core.logger import get_logger

_logger = get_logger(__name__)

# ─── 子进程注册表 ───

_tracked: list[asyncio.subprocess.Process] = []
_lock = asyncio.Lock() if sys.version_info >= (3, 10) else None


async def register_process(proc: asyncio.subprocess.Process) -> None:
    """注册子进程到追踪列表。

    Args:
        proc: asyncio subprocess 对象
    """
    _tracked.append(proc)
    _logger.debug("已追踪子进程 PID=%d", proc.pid)


async def deregister_process(proc: asyncio.subprocess.Process) -> None:
    """子进程正常结束后从追踪列表移除。

    Args:
        proc: asyncio subprocess 对象
    """
    try:
        _tracked.remove(proc)
    except ValueError:
        pass


def get_tracked_count() -> int:
    """返回当前追踪中的子进程数量。"""
    return len(_tracked)


def get_active_processes() -> list[dict]:
    """返回活跃子进程信息列表。"""
    result = []
    for proc in _tracked:
        result.append({
            "pid": proc.pid,
            "running": proc.returncode is None,
            "returncode": proc.returncode,
        })
    return result


# ─── 清理逻辑 ───


async def _kill_tree_windows(pid: int) -> None:
    """Windows：终止进程及其所有子进程（任务树）。"""
    try:
        subprocess.check_output(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        _logger.debug("已终止进程树 PID=%d", pid)
    except subprocess.CalledProcessError:
        pass  # 进程可能已自行退出
    except subprocess.TimeoutExpired:
        _logger.warning("终止进程 PID=%d 超时", pid)
    except FileNotFoundError:
        # taskkill 不存在，回退到单进程终止
        try:
            proc = await asyncio.create_subprocess_exec("taskkill", "/PID", str(pid), "/F")
            await proc.wait()
        except Exception:
            pass


async def _kill_unix(proc: asyncio.subprocess.Process) -> None:
    """Unix：终止进程组。"""
    try:
        os.killpg(os.getpgid(proc.pid), 15)  # SIGTERM
        await asyncio.wait_for(proc.wait(), timeout=3.0)
    except (ProcessLookupError, OSError, asyncio.TimeoutError):
        try:
            os.killpg(os.getpgid(proc.pid), 9)  # SIGKILL
            await asyncio.wait_for(proc.wait(), timeout=2.0)
        except (ProcessLookupError, OSError, asyncio.TimeoutError):
            pass


async def cleanup_all_processes() -> None:
    """清理所有追踪中的活跃子进程。

    Windows：使用 taskkill /T 终止进程树
    Unix：使用 SIGTERM → SIGKILL 终止进程组
    """
    if not _tracked:
        return

    _logger.info("正在清理 %d 个追踪中的子进程...", len(_tracked))

    # 先收集需要清理的进程（避免遍历时列表变化）
    to_clean = [p for p in _tracked if p.returncode is None]

    if not to_clean:
        _tracked.clear()
        return

    # 并发清理
    tasks = []
    for proc in to_clean:
        if sys.platform == "win32":
            tasks.append(_kill_tree_windows(proc.pid))
        else:
            tasks.append(_kill_unix(proc))

    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                _logger.warning("清理子进程失败: %s", r)

    _tracked.clear()
    _logger.info("子进程清理完成")


def _sync_cleanup():
    """同步版清理（atexit 回退用）。

    atexit 中事件循环可能已关闭，无法使用 async。
    """
    to_clean = [p for p in _tracked if p.returncode is None]
    if not to_clean:
        return

    _logger.info("atexit: 清理 %d 个子进程", len(to_clean))

    for proc in to_clean:
        try:
            if proc.returncode is not None:
                continue
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
            else:
                os.killpg(os.getpgid(proc.pid), 9)
        except Exception:
            pass

    _tracked.clear()


# ─── 辅助：创建追踪子进程 ───

async def create_tracked_subprocess(
    cmd: str,
    **kwargs,
) -> asyncio.subprocess.Process:
    """创建子进程并自动注册追踪。

    等价于 asyncio.create_subprocess_shell，但会自动追踪。
    Windows 默认使用 CREATE_NEW_PROCESS_GROUP 防止孤儿。

    Args:
        cmd: shell 命令
        **kwargs: 传给 asyncio.create_subprocess_shell 的参数

    Returns:
        asyncio.subprocess.Process 对象
    """
    if sys.platform == "win32":
        flags = kwargs.pop("creationflags", 0)
        flags |= subprocess.CREATE_NEW_PROCESS_GROUP
        kwargs["creationflags"] = flags

    proc = await asyncio.create_subprocess_shell(cmd, **kwargs)
    await register_process(proc)
    return proc


# ─── 自动注册 atexit 回退 ───
atexit.register(_sync_cleanup)


__all__ = [
    "register_process",
    "deregister_process",
    "cleanup_all_processes",
    "get_tracked_count",
    "get_active_processes",
    "create_tracked_subprocess",
]
