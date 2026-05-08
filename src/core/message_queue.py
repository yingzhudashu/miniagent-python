"""多消息队列管理器

支持两种模式：
- queue: 队列模式（默认），消息逐个按顺序处理，互不影响
- preemptive: 打断模式，最新消息打断正在处理的任务，队列清空

每个聊天室独立队列，CLI 使用专用 chat_id "__cli__"。
"""

from __future__ import annotations

import asyncio
import enum
from typing import Any


class QueueMode(str, enum.Enum):
    QUEUE = "queue"
    PREEMPTIVE = "preemptive"


class _ChatQueue:
    """单个聊天室的队列。"""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._processing = False
        self._queue: list[asyncio.Task] = []
        self._current_task: asyncio.Task | None = None

    @property
    def pending(self) -> int:
        return len(self._queue)

    @property
    def is_busy(self) -> bool:
        return self._processing or self._current_task is not None

    async def enqueue(
        self,
        coro,
        mode: QueueMode,
        on_start=None,
        on_done=None,
    ) -> None:
        """将协程加入队列。"""
        if mode == QueueMode.PREEMPTIVE:
            # 打断模式：取消当前任务 + 清空队列
            if self._current_task and not self._current_task.done():
                self._current_task.cancel()
            self._queue.clear()
            # 直接执行新任务
            self._current_task = asyncio.create_task(coro)
            try:
                if on_start:
                    on_start()
                await self._current_task
                if on_done:
                    on_done()
            except asyncio.CancelledError:
                pass
            finally:
                self._current_task = None
        else:
            # 队列模式：加入队列，顺序处理
            task = asyncio.create_task(self._run_sequential(coro, on_start, on_done))
            self._queue.append(task)

    async def _run_sequential(self, coro, on_start, on_done) -> None:
        """顺序执行队列中的任务。"""
        async with self._lock:
            self._processing = True
            try:
                if on_start:
                    on_start()
                await coro
                if on_done:
                    on_done()
            except asyncio.CancelledError:
                pass
            finally:
                self._processing = False


class MessageQueueManager:
    """全局消息队列管理器。"""

    CLI_CHAT_ID = "__cli__"

    def __init__(self) -> None:
        self._mode = QueueMode.QUEUE
        self._queues: dict[str, _ChatQueue] = {}

    @property
    def mode(self) -> QueueMode:
        return self._mode

    @mode.setter
    def mode(self, value: QueueMode) -> None:
        self._mode = value

    def _get_queue(self, chat_id: str) -> _ChatQueue:
        if chat_id not in self._queues:
            self._queues[chat_id] = _ChatQueue()
        return self._queues[chat_id]

    async def dispatch(self, chat_id: str, coro, on_start=None, on_done=None) -> None:
        """分发消息到指定聊天室队列。"""
        q = self._get_queue(chat_id)
        await q.enqueue(coro, self._mode, on_start, on_done)

    async def dispatch_cli(self, coro, on_start=None, on_done=None) -> None:
        """CLI 专用分发（使用内部 chat_id）。"""
        await self.dispatch(self.CLI_CHAT_ID, coro, on_start, on_done)

    def get_status(self) -> dict[str, Any]:
        """获取所有队列状态。"""
        chats = {}
        for cid, q in self._queues.items():
            label = "CLI" if cid == self.CLI_CHAT_ID else cid
            chats[label] = {
                "busy": q.is_busy,
                "pending": q.pending,
            }
        return {
            "mode": self._mode.value,
            "chats": chats,
        }


# 全局单例
message_queue = MessageQueueManager()
