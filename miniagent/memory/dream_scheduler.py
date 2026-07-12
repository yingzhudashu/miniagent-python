"""类 AutoDream 的记忆维护：周期 + 体量闸门。

在每次 agent 回合结束后由引擎触发；带最短间隔节流，避免每轮创建过多后台任务。
跨进程精炼互斥使用 ``memory/dream.lock``。

与三层记忆中「夜间精炼」叙事对应，见 ``docs/MEMORY_SYSTEM.md``。

状态根目录统一由 ``infrastructure.paths.resolve_state_dir()`` 解析。
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone
from typing import Any

from miniagent.infrastructure.json_config import get_config
from miniagent.infrastructure.logger import get_logger
from miniagent.infrastructure.paths import resolve_state_dir as get_state_root
from miniagent.infrastructure.persistence import dump_state_file, load_state_file
from miniagent.infrastructure.state_schemas import install_builtin_state_schemas
from miniagent.memory.layered_memory import (
    append_session_day_rollup,
    load_agent_longterm,
    load_session_longterm,
    save_agent_longterm,
    save_session_longterm,
)

_logger = get_logger(__name__)
install_builtin_state_schemas()

_STATE_NAME = "dream_state.json"


# 使用统一的 get_state_root() 函数获取状态根目录


# 从JSON配置获取默认值（环境变量覆盖由JsonConfigLoader自动处理）
DIARY_REFINE_SEC = get_config("dream.diary_refine_sec", 7 * 86400)
SESSION_LT_REFINE_SEC = get_config("dream.session_lt_refine_sec", 30 * 86400)
AGENT_LT_REFINE_SEC = get_config("dream.agent_lt_refine_sec", 365 * 86400)

# 体量闸门：超过则忽略最小间隔立刻标记需要精炼（由后台任务合并去重）
SIZE_FORCE_BYTES = get_config("dream.size_force_bytes", 800_000)

# 两次调度之间的最短间隔（秒），减轻每回合 create_task 压力
_MIN_SCHEDULE_INTERVAL = float(get_config("dream.min_schedule_interval_sec", 60.0) or 60.0)

def _state_path(state_root: str | None = None) -> str:
    """``memory/dream_state.json`` 绝对路径（确保 ``memory`` 目录存在）。"""
    root = state_root or get_state_root()
    d = os.path.join(root, "memory")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, _STATE_NAME)


def _load_dream_state(state_root: str | None = None) -> dict[str, Any]:
    """读取 dream 状态 JSON；不存在或损坏时返回空 dict。"""
    p = _state_path(state_root)
    if not os.path.isfile(p):
        return {}
    try:
        return load_state_file("dream_state", p)
    except Exception:
        return {}


def _save_dream_state(data: dict[str, Any], state_root: str | None = None) -> None:
    """原子写回 dream 状态（失败仅 debug 日志）。"""
    try:
        dump_state_file("dream_state", _state_path(state_root), data)
    except OSError as e:
        _logger.debug("dream_state 写入失败: %s", e)


def _diary_dir_size(session_key: str, state_root: str | None = None) -> int:
    """估算某会话 ``memory/diary/<safe>`` 下文件总字节数（体量闸门用）。"""
    from miniagent.utils.session_id import safe_session_id

    root = os.path.join(
        state_root or get_state_root(),
        "memory",
        "diary",
        safe_session_id(session_key),
    )
    if not os.path.isdir(root):
        return 0
    total = 0
    for name in os.listdir(root):
        fp = os.path.join(root, name)
        if os.path.isfile(fp):
            try:
                total += os.path.getsize(fp)
            except OSError as e:
                _logger.debug("获取文件大小失败: %s", e)
    return total


def _try_file_lock(state_root: str | None = None) -> bool:
    """跨进程互斥：独占文件 + PID（不保证崩溃后强一致，与实例注册表策略一致）。"""
    from miniagent.infrastructure.instance import is_process_running

    lock = os.path.join(state_root or get_state_root(), "memory", "dream.lock")
    os.makedirs(os.path.dirname(lock), exist_ok=True)
    for _ in range(3):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return True
        except FileExistsError:
            try:
                with open(lock, encoding="utf-8") as f:
                    pid = int(f.read().strip() or "0")
            except Exception:
                return False
            if pid and pid != os.getpid() and is_process_running(pid):
                return False
            try:
                os.unlink(lock)
            except OSError:
                return False
    return False


def _release_file_lock(state_root: str | None = None) -> None:
    """若锁文件由本 PID 持有则删除，释放跨进程 dream 互斥。"""
    lock = os.path.join(state_root or get_state_root(), "memory", "dream.lock")
    try:
        if os.path.isfile(lock):
            with open(lock, encoding="utf-8") as f:
                owner_pid = f.read().strip()
            # Windows 不允许删除仍由当前进程打开的文件，因此必须先退出读取上下文。
            if owner_pid == str(os.getpid()):
                os.unlink(lock)
    except OSError as e:
        _logger.debug("释放锁文件失败: %s", e)


async def _refine_session(session_key: str, state_root: str | None = None) -> None:
    """合并日记体量信号、更新 session_lt 日索引占位、压缩 agent_lt 列表长度。"""
    st = _load_dream_state(state_root)
    sk = st.setdefault("per_session", {})
    ent = sk.setdefault(session_key, {})
    now = time.time()

    diary_sz = _diary_dir_size(session_key, state_root)
    force = diary_sz >= SIZE_FORCE_BYTES

    last_d = float(ent.get("last_diary_refine", 0) or 0)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if force or now - last_d >= DIARY_REFINE_SEC:
        if ent.get("last_rollup_day") == day:
            ent["last_diary_refine"] = now
        else:
            from miniagent.utils.session_id import safe_session_id

            abs_d = os.path.join(
                state_root or get_state_root(),
                "memory",
                "diary",
                safe_session_id(session_key),
                f"{day}.md",
            )
            try:
                rel = os.path.relpath(abs_d, state_root or get_state_root()).replace("\\", "/")
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
    if force or now - last_s >= SESSION_LT_REFINE_SEC:
        doc = load_session_longterm(session_key)
        days = doc.get("day_entries") or []
        if len(days) > 200:
            doc["day_entries"] = days[-120:]
            save_session_longterm(session_key, doc)
        ent["last_session_lt_refine"] = now

    last_a = float(st.get("last_agent_lt_refine", 0) or 0)
    if force or now - last_a >= AGENT_LT_REFINE_SEC:
        ag = load_agent_longterm()
        items = ag.get("entries") or []
        if len(items) > 500:
            ag["entries"] = items[-300:]
            save_agent_longterm(ag)
        st["last_agent_lt_refine"] = now

    sk[session_key] = ent
    _save_dream_state(st, state_root)


class DreamScheduler:
    """Own throttling state and maintenance tasks for one memory runtime."""

    def __init__(self, state_root: str) -> None:
        self._state_root = state_root
        self._last_schedule_monotonic = 0.0
        self._pending_tasks: set[asyncio.Task[Any]] = set()

    def schedule(self, session_key: str | None) -> None:
        """Schedule non-blocking maintenance after a completed agent turn."""
        if not session_key:
            return
        now = time.monotonic()
        if now - self._last_schedule_monotonic < _MIN_SCHEDULE_INTERVAL:
            return
        self._last_schedule_monotonic = now
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        async def _job() -> None:
            if not _try_file_lock(self._state_root):
                return
            try:
                await _refine_session(session_key, self._state_root)
            finally:
                _release_file_lock(self._state_root)

        task = loop.create_task(_job())
        self._pending_tasks.add(task)

        def _done(completed: asyncio.Task[Any]) -> None:
            self._pending_tasks.discard(completed)
            if completed.cancelled():
                return
            error = completed.exception()
            if error is not None:
                _logger.error("记忆维护任务异常: %s", error, exc_info=error)

        task.add_done_callback(_done)

    async def shutdown(self) -> None:
        """Cancel and await all maintenance tasks owned by this scheduler."""
        pending = [task for task in self._pending_tasks if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._pending_tasks.clear()


__all__ = [
    "DreamScheduler",
    "DIARY_REFINE_SEC",
    "SESSION_LT_REFINE_SEC",
    "AGENT_LT_REFINE_SEC",
]
