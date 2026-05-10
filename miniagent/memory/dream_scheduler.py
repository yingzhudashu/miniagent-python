"""类 AutoDream 的记忆维护：周期 + 体量闸门。

在每次 agent 回合结束后由引擎触发；带最短间隔节流，避免每轮创建过多后台任务。
跨进程精炼互斥使用 ``memory/dream.lock``。"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from typing import Any

from miniagent.infrastructure.logger import get_logger
from miniagent.memory.layered_memory import (
    append_session_day_rollup,
    load_session_longterm,
    load_agent_longterm,
    save_agent_longterm,
    save_session_longterm,
)

_logger = get_logger(__name__)

_STATE_NAME = "dream_state.json"


def _state_dir() -> str:
    return os.environ.get("MINI_AGENT_STATE", os.path.join(os.getcwd(), "workspaces"))

# 默认周期（秒）
DIARY_REFINE_SEC = int(os.environ.get("MINI_AGENT_DREAM_DIARY_SEC", str(7 * 86400)))
SESSION_LT_REFINE_SEC = int(os.environ.get("MINI_AGENT_DREAM_SESSION_LT_SEC", str(30 * 86400)))
AGENT_LT_REFINE_SEC = int(os.environ.get("MINI_AGENT_DREAM_AGENT_LT_SEC", str(365 * 86400)))

# 体量闸门：超过则忽略最小间隔立刻标记需要精炼（由后台任务合并去重）
SIZE_FORCE_BYTES = int(os.environ.get("MINI_AGENT_DREAM_SIZE_BYTES", str(800_000)))

# 两次调度之间的最短间隔（秒），减轻每回合 create_task 压力
_MIN_SCHEDULE_INTERVAL = float(
    os.environ.get("MINI_AGENT_DREAM_MIN_INTERVAL_SEC", "60") or "60"
)
_last_schedule_monotonic: float = 0.0


def _state_path() -> str:
    root = _state_dir()
    d = os.path.join(root, "memory")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, _STATE_NAME)


def _load_dream_state() -> dict[str, Any]:
    p = _state_path()
    if not os.path.isfile(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_dream_state(data: dict[str, Any]) -> None:
    try:
        with open(_state_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError as e:
        _logger.debug("dream_state 写入失败: %s", e)


def _diary_dir_size(session_key: str) -> int:
    from miniagent.memory.history_archive import safe_session_id_for_memory

    root = os.path.join(
        _state_dir(),
        "memory",
        "diary",
        safe_session_id_for_memory(session_key),
    )
    if not os.path.isdir(root):
        return 0
    total = 0
    for name in os.listdir(root):
        fp = os.path.join(root, name)
        if os.path.isfile(fp):
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
    return total


def _try_file_lock() -> bool:
    """跨进程互斥：独占文件 + PID（不保证崩溃后强一致，与实例注册表策略一致）。"""
    from miniagent.infrastructure.instance import _is_process_running

    lock = os.path.join(_state_dir(), "memory", "dream.lock")
    os.makedirs(os.path.dirname(lock), exist_ok=True)
    for _ in range(3):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return True
        except FileExistsError:
            try:
                with open(lock, "r", encoding="utf-8") as f:
                    pid = int(f.read().strip() or "0")
            except Exception:
                return False
            if pid and pid != os.getpid() and _is_process_running(pid):
                return False
            try:
                os.unlink(lock)
            except OSError:
                return False
    return False


def _release_file_lock() -> None:
    lock = os.path.join(_state_dir(), "memory", "dream.lock")
    try:
        if os.path.isfile(lock):
            with open(lock, "r", encoding="utf-8") as f:
                if f.read().strip() == str(os.getpid()):
                    os.unlink(lock)
    except OSError:
        pass


async def _refine_session(session_key: str) -> None:
    """合并日记体量信号、更新 session_lt 日索引占位、压缩 agent_lt 列表长度。"""
    st = _load_dream_state()
    sk = st.setdefault("per_session", {})
    ent = sk.setdefault(session_key, {})
    now = time.time()

    diary_sz = _diary_dir_size(session_key)
    force = diary_sz >= SIZE_FORCE_BYTES

    last_d = float(ent.get("last_diary_refine", 0) or 0)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if not force and now - last_d < DIARY_REFINE_SEC:
        pass
    elif ent.get("last_rollup_day") == day:
        ent["last_diary_refine"] = now
    else:
        from miniagent.memory.history_archive import diary_file_path

        abs_d = diary_file_path(session_key, day)
        try:
            rel = os.path.relpath(abs_d, _state_dir()).replace("\\", "/")
        except ValueError:
            rel = abs_d.replace("\\", "/")
        append_session_day_rollup(
            session_key,
            day=day,
            diary_relative=rel,
            summary=f"日记体量约 {diary_sz} 字节，已登记索引（精炼占位）。",
        )
        ent["last_diary_refine"] = now
        ent["last_rollup_day"] = day

    last_s = float(ent.get("last_session_lt_refine", 0) or 0)
    if not force and now - last_s < SESSION_LT_REFINE_SEC:
        pass
    else:
        doc = load_session_longterm(session_key)
        days = doc.get("day_entries") or []
        if len(days) > 200:
            doc["day_entries"] = days[-120:]
            save_session_longterm(session_key, doc)
        ent["last_session_lt_refine"] = now

    last_a = float(st.get("last_agent_lt_refine", 0) or 0)
    if not force and now - last_a < AGENT_LT_REFINE_SEC:
        pass
    else:
        ag = load_agent_longterm()
        items = ag.get("entries") or []
        if len(items) > 500:
            ag["entries"] = items[-300:]
            save_agent_longterm(ag)
        st["last_agent_lt_refine"] = now

    sk[session_key] = ent
    _save_dream_state(st)


def schedule_memory_maintenance(session_key: str | None) -> None:
    """在 agent 回合结束后调用；不阻塞用户输入。"""
    global _last_schedule_monotonic  # noqa: PLW0603

    if not session_key:
        return
    now_m = time.monotonic()
    if now_m - _last_schedule_monotonic < _MIN_SCHEDULE_INTERVAL:
        return
    _last_schedule_monotonic = now_m

    async def _job() -> None:
        if not _try_file_lock():
            return
        try:
            await _refine_session(session_key)
        finally:
            _release_file_lock()

    try:
        asyncio.create_task(_job())
    except RuntimeError:
        # 无 running loop（极少）
        pass


__all__ = ["schedule_memory_maintenance", "DIARY_REFINE_SEC", "SESSION_LT_REFINE_SEC", "AGENT_LT_REFINE_SEC"]
