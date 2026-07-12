"""子进程生命周期追踪器

防止主进程退出时遗留孤儿进程。

用法：
    from miniagent.infrastructure.process import create_tracked_subprocess, cleanup_all_processes

    proc = await create_tracked_subprocess("long_running_cmd")
    # ... 主进程退出时自动调用 cleanup_all_processes()
"""

from __future__ import annotations

import asyncio
import atexit
import os
import subprocess
import sys
from typing import Any, cast

from miniagent.infrastructure.logger import get_logger

_logger = get_logger(__name__)

# ─── 子进程注册表 ───

_tracked: list[asyncio.subprocess.Process] = []
_lock = asyncio.Lock() if sys.version_info >= (3, 10) else None


async def register_process(proc: asyncio.subprocess.Process) -> None:
    """注册子进程到追踪列表。

    Args:
        proc: asyncio subprocess 对象
    """
    if _lock is not None:
        async with _lock:
            _tracked.append(proc)
    else:
        _tracked.append(proc)
    _logger.debug("已追踪子进程 PID=%d", proc.pid)


async def deregister_process(proc: asyncio.subprocess.Process) -> None:
    """子进程正常结束后从追踪列表移除。

    Args:
        proc: asyncio subprocess 对象
    """
    try:
        if _lock is not None:
            async with _lock:
                _tracked.remove(proc)
        else:
            _tracked.remove(proc)
    except ValueError:
        _logger.debug("子进程已从追踪列表移除或不存在: PID=%d", proc.pid)


def get_tracked_count() -> int:
    """返回当前追踪中的子进程数量。"""
    return len(_tracked)


def get_active_processes() -> list[dict]:
    """返回活跃子进程信息列表。"""
    result = []
    for proc in _tracked:
        result.append(
            {
                "pid": proc.pid,
                "running": proc.returncode is None,
                "returncode": proc.returncode,
            }
        )
    return result


# ─── 清理逻辑 ───


async def _kill_tree_windows(pid: int) -> None:
    """Windows：终止进程及其所有子进程（任务树）。"""
    try:
        taskkill = await asyncio.create_subprocess_exec(
            "taskkill",
            "/PID",
            str(pid),
            "/T",
            "/F",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            return_code = await asyncio.wait_for(taskkill.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            taskkill.kill()
            await taskkill.wait()
            raise
        if return_code == 0:
            _logger.debug("已终止进程树 PID=%d", pid)
        else:
            _logger.debug("进程可能已自行退出: PID=%d, code=%d", pid, return_code)
    except asyncio.TimeoutError:
        _logger.warning("终止进程 PID=%d 超时", pid)
    except FileNotFoundError:
        _logger.debug("taskkill 不可用，无法终止 Windows 进程树 PID=%d", pid)


async def _kill_unix(proc: asyncio.subprocess.Process) -> None:
    """Unix：终止进程组。"""
    # Windows 的 typeshed 不暴露这两个 POSIX API；该函数只在 Unix 分支调用。
    posix_os = cast(Any, os)
    getpgid = posix_os.getpgid
    killpg = posix_os.killpg
    try:
        pgid = getpgid(proc.pid)
        killpg(pgid, 15)  # SIGTERM
        await asyncio.wait_for(proc.wait(), timeout=3.0)
    except asyncio.TimeoutError:
        try:
            pgid = getpgid(proc.pid)
            killpg(pgid, 9)  # SIGKILL
            await asyncio.wait_for(proc.wait(), timeout=2.0)
        except (ProcessLookupError, OSError, asyncio.TimeoutError) as e:
            _logger.debug("强制终止进程失败: %s", e)
    except (ProcessLookupError, OSError) as e:
        _logger.debug("进程已退出或进程组不存在: %s", e)


async def cleanup_all_processes() -> None:
    """清理所有追踪中的活跃子进程。

    Windows：使用 taskkill /T 终止进程树
    Unix：使用 SIGTERM → SIGKILL 终止进程组

    注意：会先尝试 communicate() 以关闭管道，避免 Windows asyncio "unclosed transport" 警告。
    """
    if not _tracked:
        return

    _logger.info("正在清理 %d 个追踪中的子进程...", len(_tracked))

    # 先收集需要清理的进程（避免遍历时列表变化）
    to_clean = [p for p in _tracked if p.returncode is None]

    if not to_clean:
        _tracked.clear()
        return

    # 先尝试 communicate() 关闭管道（超时 0.5s）
    async def _close_pipes(proc: asyncio.subprocess.Process) -> None:
        try:
            # communicate() 会关闭 stdin/stdout/stderr 管道
            await asyncio.wait_for(proc.communicate(), timeout=0.5)
        except asyncio.TimeoutError:
            _logger.debug("关闭管道超时，继续 kill 逻辑")
        except Exception as e:
            _logger.debug("终止进程回退失败: %s", e)

    await asyncio.gather(*[_close_pipes(p) for p in to_clean], return_exceptions=True)

    # 并行清理
    tasks = []
    for proc in to_clean:
        if proc.returncode is None:  # 可能已被 communicate 关闭
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
                pgid = os.getpgid(proc.pid)
                os.killpg(pgid, 9)  # SIGKILL
        except (ProcessLookupError, OSError) as e:
            _logger.debug("进程已退出或进程组不存在: %s", e)
        except Exception as e:
            _logger.debug("终止进程回退失败: %s", e)

    _tracked.clear()


# ─── 辅助：创建追踪子进程 ───


async def create_tracked_subprocess(
    cmd: str,
    **kwargs,
) -> asyncio.subprocess.Process:
    """创建子进程并自动注册追踪。

    等价于 asyncio.create_subprocess_shell，但会自动追踪。
    Windows 默认使用 CREATE_NEW_PROCESS_GROUP 防止孤儿。
    Unix 默认使用 start_new_session 创建新进程组。

    注意：Windows 上 CREATE_NEW_PROCESS_GROUP 与管道不兼容。
    如果调用方传入 stdout/stderr 管道，将自动移除该标志。

    Args:
        cmd: shell 命令
        **kwargs: 传给 asyncio.create_subprocess_shell 的参数

    Returns:
        asyncio.subprocess.Process 对象
    """
    if sys.platform == "win32":
        flags = kwargs.pop("creationflags", 0)
        # Windows: CREATE_NEW_PROCESS_GROUP 与管道不兼容，如果调用方需要管道则不添加该标志
        has_pipes = (
            kwargs.get("stdout") is not None
            or kwargs.get("stderr") is not None
            or kwargs.get("stdin") is not None
        )
        if not has_pipes:
            flags |= subprocess.CREATE_NEW_PROCESS_GROUP
        kwargs["creationflags"] = flags
    else:
        # Unix: 创建新进程组以便 killpg 能正确终止整个进程树
        kwargs["start_new_session"] = True

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
