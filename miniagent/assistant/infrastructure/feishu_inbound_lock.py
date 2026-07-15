"""飞书入站独占锁 — 同一状态目录下仅一个存活进程可持有飞书 WebSocket。

用于多开 CLI 时避免多个进程同时连接同一飞书应用导致事件重复或状态错乱。
死亡 PID 的锁文件会被后续 ``try_acquire`` 覆盖。

获取/释放均在 ``.feishu_inbound.lock`` 文件锁保护下进行，避免并发 ``try_acquire`` 竞态。

**重构说明**：
- PID 检测函数已提取到公共模块 ``miniagent/infrastructure/process_utils.py``
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

from miniagent.agent.logging import get_logger
from miniagent.agent.types.error_prefix import ERROR_PREFIX
from miniagent.assistant.infrastructure.process_utils import is_process_running

_logger = get_logger(__name__)

_LOCK_FILENAME = "feishu_inbound_owner.json"
_CROSS_PROCESS_LOCK = ".feishu_inbound.lock"


def _state_root(state_dir: str | None) -> Path:
    """解析状态根路径（显式参数优先，否则从配置读取）。"""
    from miniagent.assistant.infrastructure.paths import resolve_state_dir

    root = state_dir or resolve_state_dir()
    return Path(root)


@contextlib.contextmanager
def _state_dir_file_lock(base: Path) -> Iterator[None]:
    """跨进程互斥锁：保护入站 owner 文件的读-改-写。"""
    lock_path = base / _CROSS_PROCESS_LOCK
    base.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+b") as lock_f:
        try:
            if sys.platform == "win32":
                lock_f.seek(0)
                msvcrt.locking(lock_f.fileno(), msvcrt.LK_LOCK, 1)
            else:
                fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            if sys.platform == "win32":
                try:
                    lock_f.seek(0)
                    msvcrt.locking(lock_f.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError as e:
                    _logger.debug("Windows 飞书锁解锁失败: %s", e)
            else:
                try:
                    fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)
                except OSError as e:
                    _logger.debug("Unix 飞书锁解锁失败: %s", e)


def try_acquire_feishu_inbound_owner(
    *,
    state_dir: str | None = None,
    instance_id: int | None = None,
) -> tuple[bool, str]:
    """尝试成为飞书入站唯一持有者。

    Returns:
        (True, "") 成功；(False, 人类可读原因) 失败。
    """
    base = _state_root(state_dir)
    path = base / _LOCK_FILENAME
    me = os.getpid()
    payload: dict[str, Any] = {
        "pid": me,
        "instance_id": instance_id,
        "claimed_at": time.time(),
    }
    with _state_dir_file_lock(base):
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                raw = {}
            old_pid = int(raw.get("pid") or 0)
            if old_pid and old_pid != me and is_process_running(old_pid):
                oid = raw.get("instance_id", "?")
                return (
                    False,
                    f"{ERROR_PREFIX} 飞书入站已被实例 #{oid}（PID={old_pid}）占用；"
                    "请在该实例执行 `.feishu stop` 或停止该进程后再试。",
                )
        try:
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError as e:
            return False, f"{ERROR_PREFIX} 无法写入飞书锁文件: {e}"
    _logger.info("已获取飞书入站独占锁 (PID=%s)", me)
    return True, ""


def read_feishu_inbound_owner(
    state_dir: str | None = None,
) -> dict[str, Any] | None:
    """读取当前锁文件内容（若存在）；含 ``alive`` 字段表示 PID 是否仍在运行。"""
    base = _state_root(state_dir)
    path = base / _LOCK_FILENAME
    if not path.is_file():
        return None
    with _state_dir_file_lock(base):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        pid = int(raw.get("pid") or 0)
        raw["alive"] = bool(pid and is_process_running(pid))
        return raw


def release_feishu_inbound_owner(state_dir: str | None = None) -> None:
    """释放本进程持有的飞书入站锁（若匹配）。"""
    base = _state_root(state_dir)
    path = base / _LOCK_FILENAME
    if not path.exists():
        return
    with _state_dir_file_lock(base):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if int(raw.get("pid") or 0) == os.getpid():
                try:
                    path.unlink()
                except FileNotFoundError as e:
                    _logger.debug("锁文件已不存在: %s", e)
        except FileNotFoundError:
            return
        except Exception as e:
            _logger.debug("释放飞书锁时忽略: %s", e)


__all__ = [
    "try_acquire_feishu_inbound_owner",
    "release_feishu_inbound_owner",
    "read_feishu_inbound_owner",
]
