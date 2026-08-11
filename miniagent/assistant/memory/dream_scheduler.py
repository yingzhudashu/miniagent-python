"""类 AutoDream 的记忆维护：周期 + 体量闸门。

在每次 agent 回合结束后由引擎触发；带最短间隔节流，避免每轮创建过多后台任务。
跨进程精炼互斥和维护游标存储在项目 SQLite 数据库。

与三层记忆中「夜间精炼」叙事对应，见 ``docs/MEMORY_SYSTEM.md``。

状态根目录统一由 ``infrastructure.paths.resolve_state_dir()`` 解析。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from miniagent.agent.logging import get_logger
from miniagent.assistant.infrastructure.json_config import get_config
from miniagent.assistant.infrastructure.paths import resolve_state_dir as get_state_root
from miniagent.assistant.memory.layered_memory import (
    append_session_day_rollup,
    load_agent_longterm,
    load_session_longterm,
    save_agent_longterm,
    save_session_longterm,
)
from miniagent.assistant.state.sync import immediate_transaction, open_state_database

_logger = get_logger(__name__)
_STATE_KEY = "memory:dream"
_LEASE_RESOURCE = "maintenance:dream"
_LEASE_TTL_MS = 10 * 60 * 1000


# 使用统一的 get_state_root() 函数获取状态根目录


# 从JSON配置获取默认值（环境变量覆盖由JsonConfigLoader自动处理）
DIARY_REFINE_SEC = 7 * 86400
SESSION_LT_REFINE_SEC = 30 * 86400
AGENT_LT_REFINE_SEC = 365 * 86400

# 体量闸门：超过则忽略最小间隔立刻标记需要精炼（由后台任务合并去重）
SIZE_FORCE_BYTES = 800_000

# 两次调度之间的最短间隔（秒），减轻每回合 create_task 压力
_MIN_SCHEDULE_INTERVAL = 60.0


@dataclass(frozen=True, slots=True)
class _DreamPolicy:
    diary_refine_seconds: float
    session_refine_seconds: float
    agent_refine_seconds: float
    size_force_bytes: int
    min_schedule_interval: float

    @classmethod
    def from_config(cls) -> _DreamPolicy:
        return cls(
            diary_refine_seconds=float(
                get_config("dream.diary_refine_sec", DIARY_REFINE_SEC)
            ),
            session_refine_seconds=float(
                get_config("dream.session_lt_refine_sec", SESSION_LT_REFINE_SEC)
            ),
            agent_refine_seconds=float(
                get_config("dream.agent_lt_refine_sec", AGENT_LT_REFINE_SEC)
            ),
            size_force_bytes=int(get_config("dream.size_force_bytes", SIZE_FORCE_BYTES)),
            min_schedule_interval=float(
                get_config("dream.min_schedule_interval_sec", _MIN_SCHEDULE_INTERVAL)
                or _MIN_SCHEDULE_INTERVAL
            ),
        )


def _load_dream_state(state_root: str | None = None) -> dict[str, Any]:
    """Read the current maintenance cursor from SQLite."""
    with open_state_database(state_root or get_state_root()) as connection:
        row = connection.execute(
            "SELECT value_json FROM maintenance_state WHERE state_key=?",
            (_STATE_KEY,),
        ).fetchone()
    if row is None:
        return {}
    value = json.loads(str(row[0]))
    if not isinstance(value, dict):
        raise ValueError("dream maintenance state must be a JSON object")
    return value


def _save_dream_state(data: dict[str, Any], state_root: str | None = None) -> None:
    """Atomically write the current maintenance cursor."""
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    with open_state_database(state_root or get_state_root()) as connection:
        connection.execute(
            """INSERT INTO maintenance_state VALUES (?, ?, ?)
               ON CONFLICT(state_key) DO UPDATE SET value_json=excluded.value_json,
                 updated_at_ms=excluded.updated_at_ms""",
            (_STATE_KEY, payload, int(time.time() * 1000)),
        )


def _diary_dir_size(session_key: str, state_root: str | None = None) -> int:
    """估算某会话 ``memory/diary/<safe>`` 下文件总字节数（体量闸门用）。"""
    from miniagent.assistant.utils.session_id import safe_session_id

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


def _try_maintenance_lease(state_root: str, owner: str) -> bool:
    now_ms = int(time.time() * 1000)
    with open_state_database(state_root) as connection:
        with immediate_transaction(connection):
            row = connection.execute(
                "SELECT owner, expires_at_ms, generation FROM process_leases WHERE resource=?",
                (_LEASE_RESOURCE,),
            ).fetchone()
            if row is not None and str(row[0]) != owner and int(row[1]) > now_ms:
                return False
            generation = int(row[2]) + 1 if row is not None else 1
            connection.execute(
                """INSERT INTO process_leases VALUES (?, ?, ?, ?)
                   ON CONFLICT(resource) DO UPDATE SET owner=excluded.owner,
                     expires_at_ms=excluded.expires_at_ms,
                     generation=excluded.generation""",
                (_LEASE_RESOURCE, owner, now_ms + _LEASE_TTL_MS, generation),
            )
    return True


def _release_maintenance_lease(state_root: str, owner: str) -> None:
    with open_state_database(state_root) as connection:
        connection.execute(
            "DELETE FROM process_leases WHERE resource=? AND owner=?",
            (_LEASE_RESOURCE, owner),
        )


def _refine_session_sync(
    session_key: str,
    state_root: str | None,
    policy: _DreamPolicy,
) -> None:
    """Perform the complete file-backed refinement on a worker thread."""
    st = _load_dream_state(state_root)
    sk = st.setdefault("per_session", {})
    ent = sk.setdefault(session_key, {})
    now = time.time()

    diary_sz = _diary_dir_size(session_key, state_root)
    force = diary_sz >= policy.size_force_bytes

    last_d = float(ent.get("last_diary_refine", 0) or 0)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if force or now - last_d >= policy.diary_refine_seconds:
        if ent.get("last_rollup_day") == day:
            ent["last_diary_refine"] = now
        else:
            from miniagent.assistant.utils.session_id import safe_session_id

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
    if force or now - last_s >= policy.session_refine_seconds:
        doc = load_session_longterm(session_key)
        days = doc.get("day_entries") or []
        if len(days) > 200:
            doc["day_entries"] = days[-120:]
            save_session_longterm(session_key, doc)
        ent["last_session_lt_refine"] = now

    last_a = float(st.get("last_agent_lt_refine", 0) or 0)
    if force or now - last_a >= policy.agent_refine_seconds:
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
        self._policy = _DreamPolicy.from_config()
        self._last_schedule_monotonic = 0.0
        self._pending_tasks: set[asyncio.Task[Any]] = set()
        self._lease_owner = f"{os.getpid()}:{id(self)}"

    def schedule(self, session_key: str | None) -> None:
        """Schedule non-blocking maintenance after a completed agent turn."""
        if not session_key:
            return
        now = time.monotonic()
        if now - self._last_schedule_monotonic < self._policy.min_schedule_interval:
            return
        self._last_schedule_monotonic = now
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        def _run_locked_refinement() -> None:
            if not _try_maintenance_lease(self._state_root, self._lease_owner):
                return
            try:
                _refine_session_sync(session_key, self._state_root, self._policy)
            finally:
                _release_maintenance_lease(self._state_root, self._lease_owner)

        async def _job() -> None:
            from miniagent.agent.observability import trace_span

            with trace_span("memory.dream_refine", session_key=session_key):
                await asyncio.to_thread(_run_locked_refinement)

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
