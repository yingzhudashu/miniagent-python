"""飞书入站独占锁 — 同一状态目录下仅一个存活进程可持有飞书 WebSocket。

用于多开 CLI 时避免多个进程同时连接同一飞书应用导致事件重复或状态错乱。
死亡 PID 的锁文件会被后续 ``try_acquire`` 覆盖。

**重构说明**：
- PID 检测函数已提取到公共模块 ``miniagent/infrastructure/process_utils.py``
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from miniagent.infrastructure.json_config import get_config
from miniagent.infrastructure.logger import get_logger
from miniagent.infrastructure.process_utils import is_process_running

_logger = get_logger(__name__)

_LOCK_FILENAME = "feishu_inbound_owner.json"


def _state_root(state_dir: str | None) -> Path:
    """解析状态根路径（显式参数优先，否则从配置读取）。"""
    root = state_dir or get_config("paths.state_dir", os.path.join(os.getcwd(), "workspaces"))
    return Path(root)


# PID 检测函数已移至 miniagent.infrastructure.process_utils
# is_process_running 从 process_utils 导入


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
        if old_pid and old_pid != me and is_process_running(old_pid):
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
    raw["alive"] = bool(pid and is_process_running(pid))
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
