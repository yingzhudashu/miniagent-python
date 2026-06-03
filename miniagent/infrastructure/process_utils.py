"""Mini Agent Python — 进程检测公共模块

提供跨平台的进程存活检测函数，用于：
- 多实例注册表存活判定（instance.py）
- 飞书入站独占锁存活判定（feishu_inbound_lock.py）

实现方式：
- Windows: 使用 tasklist 命令查询进程
- POSIX: 使用 os.kill(pid, 0) 检测进程信号

**性能注意**：
- 同步版本使用 subprocess.check_output，会阻塞调用线程
- 异步版本使用 asyncio.create_subprocess_exec，不阻塞事件循环
- 建议在异步上下文中使用 is_process_running_async
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys


def is_process_running(pid: int) -> bool:
    """检测 PID 对应的进程是否仍在运行（同步版本）

    跨平台实现：
    - Windows: 使用 tasklist 命令查询
    - POSIX: 使用 os.kill(pid, 0) 发送空信号

    Args:
        pid: 进程 ID

    Returns:
        进程是否仍在运行。若 PID <= 0 或检测失败，返回 False。

    Note:
        该函数会阻塞调用线程。在异步上下文中，建议使用
        is_process_running_async 避免阻塞事件循环。
    """
    if pid <= 0:
        return False
    try:
        if sys.platform == "win32":
            output = subprocess.check_output(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                timeout=5,
                text=True,
                stderr=subprocess.DEVNULL,
            )
            return f'"{pid}"' in output
        else:
            # POSIX 系统：发送空信号检测进程是否存在
            os.kill(pid, 0)
            return True
    except Exception:
        return False


async def is_process_running_async(pid: int) -> bool:
    """检测 PID 对应的进程是否仍在运行（异步版本）

    跨平台实现：
    - Windows: 使用 asyncio.create_subprocess_exec 执行 tasklist
    - POSIX: 使用 os.kill(pid, 0) 发送空信号（非阻塞）

    Args:
        pid: 进程 ID

    Returns:
        进程是否仍在运行。若 PID <= 0 或检测失败，返回 False。

    Note:
        该函数不会阻塞事件循环。在需要频繁检测的场景中，
        建议使用此异步版本。
    """
    if pid <= 0:
        return False
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
            # POSIX 系统：os.kill 在非 Windows 平台不会阻塞
            os.kill(pid, 0)
            return True
    except Exception:
        return False


__all__ = [
    "is_process_running",
    "is_process_running_async",
]