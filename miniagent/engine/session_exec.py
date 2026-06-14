"""会话级 Agent 执行协调 — per-session 锁与并行上限。

``parallel_sessions=true``（默认）时：不同 ``session_key`` 可并行执行，同一 ``session_key`` 串行，
且受 ``max_parallel_sessions`` 全局 Semaphore 限制。

``parallel_sessions=false`` 时：退化为单一全局 Lock，与旧版 ``UnifiedEngine._exec_lock`` 等价。

由 :class:`UnifiedEngine` 在初始化时创建，经 :meth:`SessionExecCoordinator.acquire` 或
:meth:`~miniagent.engine.engine.UnifiedEngine.session_turn` 进入 Agent 执行前获取锁。
``asyncio.Lock`` **不可重入**；若调用方已通过 ``session_turn`` 持有锁，须传
``_hold_session_lock=True`` 给 ``run_agent_with_thinking``，避免二次 ``acquire`` 死锁。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from miniagent.infrastructure.json_config import get_config


class SessionExecCoordinator:
    """协调多会话 Agent 执行的锁与并发计数。

    并行模式（``parallel_sessions=true``）下对每个 ``session_key`` 维护独立
    :class:`asyncio.Lock`，并用 :class:`asyncio.Semaphore` 限制进程内同时**正在执行**
    的 Agent 数量。Semaphore 在取得会话锁**之后**再获取，因此同一会话排队的后续任务
    不会提前占用全局并发名额，其他 ``session_key`` 仍可并行启动。

    串行模式（``parallel_sessions=false``）下忽略 ``session_key``，所有调用共用
    ``_global_lock``。

    会话锁字典 ``_session_locks`` 按 ``session_key`` 懒创建且进程生命周期内不回收；
    对稳定的 CLI / 飞书 ``session_key`` 通常可接受。
    """

    def __init__(
        self,
        *,
        parallel_sessions: bool | None = None,
        max_parallel_sessions: int | None = None,
    ) -> None:
        """初始化协调器。

        Args:
            parallel_sessions: 是否允许不同 ``session_key`` 并行执行。
                ``None`` 时读取 ``agent.parallel_sessions``（默认 ``True``）。
            max_parallel_sessions: 并行模式下进程内同时运行的 Agent 上限。
                ``None`` 时读取 ``agent.max_parallel_sessions``（默认 ``4``）；
                传入 ``0`` 或负数时会被钳制为 ``1``。
        """
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
        """是否启用 per-session 并行（``False`` 时为全局串行）。"""
        return self._parallel_sessions

    @property
    def max_parallel_sessions(self) -> int:
        """并行模式下的全局 Agent 并发上限（已钳制为至少 ``1``）。"""
        return self._max_parallel

    async def _get_session_lock(self, session_key: str) -> asyncio.Lock:
        """按 ``session_key`` 懒创建并返回会话锁（由 ``_meta_lock`` 保护字典）。"""
        async with self._meta_lock:
            lock = self._session_locks.get(session_key)
            if lock is None:
                lock = asyncio.Lock()
                self._session_locks[session_key] = lock
            return lock

    @asynccontextmanager
    async def acquire(self, session_key: str) -> AsyncIterator[None]:
        """进入 Agent 执行前获取会话锁（及并行模式下的全局 Semaphore）。

        Args:
            session_key: 会话标识符。串行模式下该参数被忽略，所有调用仍全局串行。

        Yields:
            ``None``；在 ``yield`` 期间持有锁（及 Semaphore 名额）。

        Note:
            **不可重入**：同一线程/任务对同一 ``session_key`` 嵌套 ``acquire`` 会死锁。
            调用方若已通过 :meth:`~miniagent.engine.engine.UnifiedEngine.session_turn`
            持有锁，应传 ``_hold_session_lock=True`` 跳过二次获取。

            无论 ``yield`` 正常结束、抛异常还是被取消，会话锁与 Semaphore 均会在
            ``finally`` / ``async with`` 中释放。
        """
        if not self._parallel_sessions:
            async with self._global_lock:
                yield
            return

        session_lock = await self._get_session_lock(session_key)
        async with session_lock:
            if self._semaphore is not None:
                await self._semaphore.acquire()
            try:
                yield
            finally:
                if self._semaphore is not None:
                    self._semaphore.release()


__all__ = ["SessionExecCoordinator"]
