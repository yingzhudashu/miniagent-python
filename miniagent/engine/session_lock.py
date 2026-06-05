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
import logging
import os

from miniagent.infrastructure.process_utils import is_process_running, is_process_running_async
from miniagent.session.manager import _get_workspaces_dir
from miniagent.utils.session_id import safe_session_id

_logger = logging.getLogger(__name__)


def _get_lock_path(session_id: str) -> str:
    """获取会话锁文件路径。"""
    safe = safe_session_id(session_id)
    return os.path.join(_get_workspaces_dir(), safe, ".lock")


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
            if is_process_running(locked_pid):
                return False, f"被其他实例占用 (PID={locked_pid})"
            try:
                os.unlink(lock_path)
            except OSError as e:
                _logger.debug("清理过期锁文件失败: %s", e)
        except (ValueError, OSError):
            try:
                os.unlink(lock_path)
            except OSError as e:
                _logger.debug("清理损坏锁文件失败: %s", e)

    with open(lock_path, "w") as f:
        f.write(str(my_pid))
    return True, ""


async def try_lock_session_async(session_id: str) -> tuple[bool, str]:
    """异步尝试获取会话锁（不阻塞事件循环）。

    用于异步上下文（如 CLI 命令处理）中获取锁，
    避免 subprocess.check_output 阻塞事件循环。

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
            if await is_process_running_async(locked_pid):
                return False, f"被其他实例占用 (PID={locked_pid})"
            try:
                os.unlink(lock_path)
            except OSError as e:
                _logger.debug("清理过期锁文件失败: %s", e)
        except (ValueError, OSError):
            try:
                os.unlink(lock_path)
            except OSError as e:
                _logger.debug("清理损坏锁文件失败: %s", e)

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
    except Exception as e:
        _logger.debug("释放会话锁失败: %s", e)


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
        if is_process_running(locked_pid):
            return locked_pid
    except Exception as e:
        _logger.debug("检查会话锁状态失败: %s", e)
    return None


__all__ = ["try_lock_session", "try_lock_session_async", "release_session_lock", "is_session_locked"]
