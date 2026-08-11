"""Bounded, lease-protected maintenance for layered memory.

Dream maintenance runs after completed turns, uses the process-owned SQLite
store for both its cursor and lease, and leaves user-readable diary Markdown
on disk.  It never probes or rewrites retired JSON state.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from miniagent.agent.logging import get_logger
from miniagent.assistant.infrastructure.json_config import get_config
from miniagent.assistant.memory.layered_memory import LongTermMemoryStore
from miniagent.assistant.state import StateConflictError, StateStore

_logger = get_logger(__name__)
_STATE_KEY = "memory:dream"
_LEASE_RESOURCE = "maintenance:dream"
_LEASE_TTL_SECONDS = 10 * 60.0

DIARY_REFINE_SEC = 7 * 86400
SESSION_LT_REFINE_SEC = 30 * 86400
AGENT_LT_REFINE_SEC = 365 * 86400
SIZE_FORCE_BYTES = 800_000
_MIN_SCHEDULE_INTERVAL = 60.0


@dataclass(frozen=True, slots=True)
class _DreamPolicy:
    """Validated runtime thresholds for one Dream scheduler."""

    diary_refine_seconds: float
    session_refine_seconds: float
    agent_refine_seconds: float
    size_force_bytes: int
    min_schedule_interval: float

    @classmethod
    def from_config(cls) -> _DreamPolicy:
        """Read the current Dream policy once at runtime construction."""
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


def _diary_dir_size(session_key: str, state_root: str) -> int:
    """Return the byte size of one session diary without following directories."""
    from miniagent.assistant.utils.session_id import safe_session_id

    root = os.path.join(state_root, "memory", "diary", safe_session_id(session_key))
    if not os.path.isdir(root):
        return 0
    total = 0
    for name in os.listdir(root):
        path = os.path.join(root, name)
        if os.path.isfile(path):
            try:
                total += os.path.getsize(path)
            except OSError as error:
                _logger.debug("读取日记大小失败: %s", error)
    return total


async def _refine_session(
    session_key: str,
    state_root: str,
    state_store: StateStore,
    longterm: LongTermMemoryStore,
    policy: _DreamPolicy,
) -> None:
    """Apply one complete maintenance cycle and persist its cursor last."""
    state = await state_store.load_maintenance_state(_STATE_KEY) or {}
    per_session = state.setdefault("per_session", {})
    entry = per_session.setdefault(session_key, {})
    now = time.time()

    diary_size = await asyncio.to_thread(_diary_dir_size, session_key, state_root)
    force = diary_size >= policy.size_force_bytes
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    last_diary = float(entry.get("last_diary_refine", 0) or 0)
    if force or now - last_diary >= policy.diary_refine_seconds:
        if entry.get("last_rollup_day") != day:
            from miniagent.assistant.utils.session_id import safe_session_id

            absolute = os.path.join(
                state_root,
                "memory",
                "diary",
                safe_session_id(session_key),
                f"{day}.md",
            )
            try:
                relative = os.path.relpath(absolute, state_root).replace("\\", "/")
            except ValueError:
                relative = absolute.replace("\\", "/")
            await longterm.append_session_day_rollup(
                session_key,
                day=day,
                diary_relative=relative,
                summary=f"日记体量约 {diary_size} 字节，已登记索引（精炼占位）。",
            )
            entry["last_rollup_day"] = day
        entry["last_diary_refine"] = now

    last_session = float(entry.get("last_session_lt_refine", 0) or 0)
    if force or now - last_session >= policy.session_refine_seconds:
        document = await longterm.load_session(session_key)
        days = list(document.get("day_entries") or [])
        if len(days) > 200:
            document["day_entries"] = days[-120:]
            await longterm.save_session(session_key, document)
        entry["last_session_lt_refine"] = now

    last_agent = float(state.get("last_agent_lt_refine", 0) or 0)
    if force or now - last_agent >= policy.agent_refine_seconds:
        document = await longterm.load_agent()
        items = list(document.get("entries") or [])
        if len(items) > 500:
            document["entries"] = items[-300:]
            await longterm.save_agent(document)
        state["last_agent_lt_refine"] = now

    per_session[session_key] = entry
    await state_store.save_maintenance_state(_STATE_KEY, state)


class DreamScheduler:
    """Own throttling and maintenance tasks for one memory runtime."""

    def __init__(
        self,
        state_root: str,
        state_store: StateStore,
        longterm: LongTermMemoryStore,
    ) -> None:
        self._state_root = state_root
        self._state_store = state_store
        self._longterm = longterm
        self._policy = _DreamPolicy.from_config()
        self._last_schedule_monotonic = 0.0
        self._pending_tasks: set[asyncio.Task[Any]] = set()
        self._lease_owner = f"{os.getpid()}:{id(self)}"

    def schedule(self, session_key: str | None) -> None:
        """Schedule at most one throttled maintenance task for a completed turn."""
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
        task = loop.create_task(self._run(session_key), name="memory-dream")
        self._pending_tasks.add(task)
        task.add_done_callback(self._task_done)

    async def _run(self, session_key: str) -> None:
        """Acquire the cross-process lease, refine, and release on every exit."""
        from miniagent.agent.observability import trace_span

        try:
            await self._state_store.acquire_lease(
                _LEASE_RESOURCE,
                self._lease_owner,
                now=time.time(),
                ttl=_LEASE_TTL_SECONDS,
            )
        except StateConflictError:
            return
        try:
            with trace_span("memory.dream_refine", session_key=session_key):
                await _refine_session(
                    session_key,
                    self._state_root,
                    self._state_store,
                    self._longterm,
                    self._policy,
                )
        finally:
            await self._state_store.release_lease(_LEASE_RESOURCE, self._lease_owner)

    def _task_done(self, completed: asyncio.Task[Any]) -> None:
        """Drop scheduler ownership and report non-cancellation failures."""
        self._pending_tasks.discard(completed)
        if completed.cancelled():
            return
        error = completed.exception()
        if error is not None:
            _logger.error("记忆维护任务异常: %s", error, exc_info=error)

    async def shutdown(self) -> None:
        """Cancel and await every maintenance task owned by this scheduler."""
        pending = [task for task in self._pending_tasks if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._pending_tasks.clear()


__all__ = [
    "AGENT_LT_REFINE_SEC",
    "DIARY_REFINE_SEC",
    "DreamScheduler",
    "SESSION_LT_REFINE_SEC",
    "SIZE_FORCE_BYTES",
]
