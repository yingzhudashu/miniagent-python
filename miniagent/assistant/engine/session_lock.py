"""Engine — 会话级锁管理

拆分自 unified.py。每个会话一个 .lock 文件，位于 workspaces/<session_id>/.lock。

特性：
- 跨实例锁检测（PID 存活检查）
- 过期锁自动清理（进程死亡后）
- 幂等锁定（同一进程重复锁定成功）

设计约束：
- 基于 PID 文件的**尽力互斥**（读 → 判断 → 删 → 写），非 ``fcntl.flock`` / ``O_EXCL`` 级严格锁。
  极端并发下两个进程可能同时认为加锁成功；适用于 CLI 多实例的常见场景。
- 与 ``instance.py`` 的项目级实例注册互补，详见 ``docs/SECURITY.md`` §3。
"""

from __future__ import annotations

import asyncio
import logging
import os

from miniagent.assistant.infrastructure.process_utils import (
    is_process_running,
    is_process_running_async,
)
from miniagent.assistant.session.manager import _get_workspaces_dir
from miniagent.assistant.utils.session_id import safe_session_id

_logger = logging.getLogger(__name__)


def _get_lock_path(session_id: str) -> str:
    """获取会话锁文件路径（``session_id`` 经 ``safe_session_id`` 规范化）。"""
    safe = safe_session_id(session_id)
    return os.path.join(_get_workspaces_dir(), safe, ".lock")


def _remove_lock_file(lock_path: str, *, reason: str) -> None:
    """尽力删除锁文件；失败仅记 debug 日志。"""
    try:
        os.unlink(lock_path)
    except OSError as e:
        _logger.debug("%s: %s", reason, e)


def _write_lock_file(lock_path: str, my_pid: int) -> tuple[bool, str]:
    with open(lock_path, "w") as f:
        f.write(str(my_pid))
    return True, ""


def _read_lock_pid(lock_path: str) -> int:
    with open(lock_path) as file:
        return int(file.read().strip())


def try_lock_session(session_id: str) -> tuple[bool, str]:
    """尝试获取会话锁（同步）。

    Args:
        session_id: 会话标识；写入路径前会经 ``safe_session_id`` 规范化。

    Returns:
        ``(success, reason)`` — ``success=True`` 表示锁获取成功；
        失败时 ``reason`` 为可读原因（如 ``被其他实例占用 (PID=123)``）。

    Note:
        Windows 上会通过 ``subprocess`` 检测 PID 存活，**会阻塞调用线程**。
        在 asyncio 事件循环中请使用 :func:`try_lock_session_async`。
    """
    lock_path = _get_lock_path(session_id)
    my_pid = os.getpid()
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)

    if os.path.exists(lock_path):
        try:
            locked_pid = _read_lock_pid(lock_path)
            if locked_pid == my_pid:
                return True, ""  # 我自己锁的，幂等
            if is_process_running(locked_pid):
                return False, f"被其他实例占用 (PID={locked_pid})"
            _remove_lock_file(lock_path, reason="清理过期锁文件失败")
        except (ValueError, OSError):
            _remove_lock_file(lock_path, reason="清理损坏锁文件失败")

    return _write_lock_file(lock_path, my_pid)


async def try_lock_session_async(session_id: str) -> tuple[bool, str]:
    """尝试获取会话锁（异步，不阻塞事件循环）。

    语义与 :func:`try_lock_session` 相同，仅 PID 存活检测使用
    ``is_process_running_async``，避免 ``subprocess.check_output`` 阻塞 asyncio。

    Args:
        session_id: 会话标识；写入路径前会经 ``safe_session_id`` 规范化。

    Returns:
        ``(success, reason)`` — 同 :func:`try_lock_session`。
    """
    lock_path = _get_lock_path(session_id)
    my_pid = os.getpid()
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)

    if os.path.exists(lock_path):
        try:
            locked_pid = await asyncio.to_thread(_read_lock_pid, lock_path)
            if locked_pid == my_pid:
                return True, ""  # 我自己锁的，幂等
            if await is_process_running_async(locked_pid):
                return False, f"被其他实例占用 (PID={locked_pid})"
            _remove_lock_file(lock_path, reason="清理过期锁文件失败")
        except (ValueError, OSError):
            _remove_lock_file(lock_path, reason="清理损坏锁文件失败")

    return await asyncio.to_thread(_write_lock_file, lock_path, my_pid)


def release_session_lock(session_id: str) -> None:
    """释放会话锁（仅释放当前进程持有的锁）。

    Args:
        session_id: 会话标识。

    Note:
        尽力而为：仅当锁文件 PID 与 ``os.getpid()`` 一致时才删除。
        失败时静默记录 debug 日志，无返回值；不清理他人或陈旧锁。
    """
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
    """检查会话是否被**其他存活实例**锁定。

    Args:
        session_id: 会话标识。

    Returns:
        - ``None``：无锁文件；锁由当前进程持有；占用进程已退出（陈旧锁）；
          或锁文件损坏/不可读。
        - ``int``：占用者的 PID（且该进程仍存活）。

    Note:
        本函数**不删除**陈旧锁文件；实际加锁时由
        :func:`try_lock_session` / :func:`try_lock_session_async` 负责清理。
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
