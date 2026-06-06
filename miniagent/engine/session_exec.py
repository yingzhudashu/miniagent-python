"""会话级 Agent 执行协调 — per-session 锁与并行上限。

``parallel_sessions=true``（默认）时：不同 ``session_key`` 可并行执行，同一 ``session_key`` 串行，
且受 ``max_parallel_sessions`` 全局 Semaphore 限制。

``parallel_sessions=false`` 时：退化为单一全局 Lock，与旧版 ``UnifiedEngine._exec_lock`` 等价。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from miniagent.infrastructure.json_config import get_config


class SessionExecCoordinator:
    """协调多会话 Agent 执行的锁与并发计数。"""

    def __init__(
        self,
        *,
        parallel_sessions: bool | None = None,
        max_parallel_sessions: int | None = None,
    ) -> None:
        if parallel_sessions is None:
            parallel_sessions = bool(get_config("agent.parallel_sessions", True))
        if max_parallel_sessions is None:
            max_parallel_sessions = int(get_config("agent.max_parallel_sessions", 4))
        self._parallel_sessions = parallel_sessions
        self._max_parallel = max(1, max_parallel_sessions)
        self._global_lock = asyncio.Lock()
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._meta_lock = asyncio.Lock()
        self._semaphore: asyncio.Semaphore | None = (
            asyncio.Semaphore(self._max_parallel) if self._parallel_sessions else None
        )

    @property
    def parallel_sessions(self) -> bool:
        return self._parallel_sessions

    @property
    def max_parallel_sessions(self) -> int:
        return self._max_parallel

    async def _get_session_lock(self, session_key: str) -> asyncio.Lock:
        async with self._meta_lock:
            lock = self._session_locks.get(session_key)
            if lock is None:
                lock = asyncio.Lock()
                self._session_locks[session_key] = lock
            return lock

    @asynccontextmanager
    async def acquire(self, session_key: str) -> AsyncIterator[None]:
        """进入 Agent 执行前获取会话锁（及并行模式下的全局 Semaphore）。"""
        if not self._parallel_sessions:
            async with self._global_lock:
                yield
            return

        if self._semaphore is not None:
            await self._semaphore.acquire()
        session_lock = await self._get_session_lock(session_key)
        try:
            async with session_lock:
                yield
        finally:
            if self._semaphore is not None:
                self._semaphore.release()


__all__ = ["SessionExecCoordinator"]
