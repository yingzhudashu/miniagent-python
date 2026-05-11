"""飞书入站独占锁 — 同一 ``MINI_AGENT_STATE`` 下仅一个存活进程可持有飞书 WebSocket。

用于多开 CLI 时避免多个进程同时连接同一飞书应用导致事件重复或状态错乱。
死亡 PID 的锁文件会被后续 ``try_acquire`` 覆盖。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from miniagent.infrastructure.logger import get_logger

_logger = get_logger(__name__)

_LOCK_FILENAME = "feishu_inbound_owner.json"


def _state_root(state_dir: str | None) -> Path:
    """解析状态根路径（显式参数优先，否则环境变量或 cwd/workspaces）。"""
    root = state_dir or os.environ.get(
        "MINI_AGENT_STATE", os.path.join(os.getcwd(), "workspaces")
    )
    return Path(root)


def _is_pid_alive(pid: int) -> bool:
    """跨平台探测进程是否仍存活（Windows 使用 tasklist，POSIX 使用 ``os.kill(..., 0)``）。"""
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            import subprocess

            out = subprocess.check_output(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                timeout=5,
                text=True,
                stderr=subprocess.DEVNULL,
            )
            return f'"{pid}"' in out
        os.kill(pid, 0)
        return True
    except Exception:
        return False


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
    base.mkdir(parents=True, exist_ok=True)
    path = base / _LOCK_FILENAME
    me = os.getpid()
    payload: dict[str, Any] = {
        "pid": me,
        "instance_id": instance_id,
        "claimed_at": time.time(),
    }
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            raw = {}
        old_pid = int(raw.get("pid") or 0)
        if old_pid and old_pid != me and _is_pid_alive(old_pid):
            oid = raw.get("instance_id", "?")
            return (
                False,
                f"❌ 飞书入站已被实例 #{oid}（PID={old_pid}）占用；"
                "请在该实例执行 `.feishu stop` 或停止该进程后再试。",
            )
    try:
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError as e:
        return False, f"❌ 无法写入飞书锁文件: {e}"
    _logger.info("已获取飞书入站独占锁 (PID=%s)", me)
    return True, ""


def read_feishu_inbound_owner(
    state_dir: str | None = None,
) -> dict[str, Any] | None:
    """读取当前锁文件内容（若存在）；含 ``alive`` 字段表示 PID 是否仍在运行。"""
    path = _state_root(state_dir) / _LOCK_FILENAME
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    pid = int(raw.get("pid") or 0)
    raw["alive"] = bool(pid and _is_pid_alive(pid))
    return raw


def release_feishu_inbound_owner(state_dir: str | None = None) -> None:
    """释放本进程持有的飞书入站锁（若匹配）。"""
    path = _state_root(state_dir) / _LOCK_FILENAME
    if not path.exists():
        return
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if int(raw.get("pid") or 0) == os.getpid():
            try:
                path.unlink()
            except FileNotFoundError:
                pass
    except FileNotFoundError:
        return
    except Exception as e:
        _logger.debug("释放飞书锁时忽略: %s", e)


__all__ = [
    "try_acquire_feishu_inbound_owner",
    "release_feishu_inbound_owner",
    "read_feishu_inbound_owner",
]
