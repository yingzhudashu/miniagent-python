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
    """消息处理模式。

    - QUEUE: 队列模式（默认），消息逐个按顺序处理，互不影响
    - PREEMPTIVE: 打断模式，最新消息打断正在处理的任务，队列清空
    """
    QUEUE = "queue"
    PREEMPTIVE = "preemptive"


class _ChatQueue:
    """单个聊天室的异步消息队列。

    内部使用 asyncio.Lock 保证同一聊天室的消息串行处理。
    支持两种模式：
    - 队列模式：消息入队后顺序处理，互不影响
    - 打断模式：最新消息取消当前任务，清空队列后直接执行

    每个聊天室对应一个 _ChatQueue 实例，由 MessageQueueManager 统一管理。
    """

    def __init__(self) -> None:
        """初始化聊天室队列。

        属性说明：
        - _lock: asyncio 互斥锁，保证同一聊天室消息串行处理
        - _processing: 是否正在处理消息
        - _queue: 排队等待处理的任务列表（仅队列模式使用）
        - _current_task: 当前正在执行的任务
        - _task_start_time: 当前任务开始时间（用于计算运行时长）
        """
        self._lock = asyncio.Lock()
        self._processing = False
        self._queue: list[asyncio.Task] = []
        self._current_task: asyncio.Task | None = None
        self._task_start_time: float | None = None

    @property
    def pending(self) -> int:
        """等待处理的消息数量。

        Returns:
            队列中待处理的任务数。
        """
        return len(self._queue)

    @property
    def is_busy(self) -> bool:
        """检查队列是否正在处理消息。

        Returns:
            True 如果有任务正在执行或锁已获取。
        """
        return self._processing or self._current_task is not None

    @property
    def elapsed_seconds(self) -> float | None:
        """当前任务已运行秒数。"""
        if self._task_start_time is not None:
            import time
            return time.monotonic() - self._task_start_time
        return None

    async def enqueue(
        self,
        coro,
        mode: QueueMode,
        on_start=None,
        on_done=None,
    ) -> None:
        """将协程加入队列或直接执行。

        根据模式分两种行为：
        - 队列模式：创建任务并追加到队列，由 _run_sequential 保证顺序执行
        - 打断模式：取消当前任务，清空队列，直接执行新任务

        Args:
            coro: 要执行的协程对象
            mode: 消息处理模式
            on_start: 任务开始时的回调
            on_done: 任务完成时的回调
        """
        if mode == QueueMode.PREEMPTIVE:
            # 打断模式：取消当前任务 + 清空队列，直接执行新任务
            if self._current_task and not self._current_task.done():
                self._current_task.cancel()
            self._queue.clear()
            # 直接执行新任务（不走队列）
            self._current_task = asyncio.create_task(coro)
            try:
                if on_start:
                    on_start()
                await self._current_task
                if on_done:
                    on_done()
            except asyncio.CancelledError:
                pass  # 被新任务打断，静默忽略
            finally:
                self._current_task = None
        else:
            # 队列模式：创建包装任务加入队列，由 _run_sequential 保证串行
            task = asyncio.create_task(self._run_sequential(coro, on_start, on_done))
            self._queue.append(task)

    def mark_task_start(self) -> None:
        """标记当前任务开始，记录启动时间戳。"""
        import time
        self._task_start_time = time.monotonic()

    def mark_task_end(self) -> None:
        """标记当前任务结束，清除时间戳。"""
        self._task_start_time = None

    async def _run_sequential(self, coro, on_start, on_done) -> None:
        """使用 asyncio.Lock 顺序执行任务。

        同一聊天室的多个任务通过此方法串行执行，
        保证消息处理不会出现并发竞争。

        Args:
            coro: 要执行的协程
            on_start: 开始回调
            on_done: 完成回调
        """
        async with self._lock:
            self._processing = True
            self.mark_task_start()
            try:
                if on_start:
                    on_start()
                await coro
                if on_done:
                    on_done()
            except asyncio.CancelledError:
                pass  # 被打断时静默退出
            finally:
                self._processing = False
                self.mark_task_end()


class MessageQueueManager:
    """全局消息队列管理器。

    为每个聊天室（chat_id）维护独立的 _ChatQueue 实例，
    支持统一的队列模式切换（QUEUE / PREEMPTIVE）。

    CLI 使用专用的 chat_id "__cli__"，飞书每个聊天室独立队列。

    Example:
        mq = MessageQueueManager()
        mq.mode = QueueMode.PREEMPTIVE  # 切换为打断模式
        await mq.dispatch("chat_123", handle_message())
        print(mq.get_status())
    """

    CLI_CHAT_ID = "__cli__"

    def __init__(self) -> None:
        """初始化消息队列管理器。

        初始状态为队列模式，队列为空（按需创建）。
        """
        self._mode = QueueMode.QUEUE
        self._queues: dict[str, _ChatQueue] = {}

    @property
    def mode(self) -> QueueMode:
        """当前消息处理模式。

        Returns:
            QUEUE（顺序处理）或 PREEMPTIVE（打断模式）。
        """
        return self._mode

    @mode.setter
    def mode(self, value: QueueMode) -> None:
        self._mode = value

    def _get_queue(self, chat_id: str) -> _ChatQueue:
        """获取或创建指定聊天室的队列。

        采用懒创建策略，首次访问某个 chat_id 时才创建队列。

        Args:
            chat_id: 聊天室标识

        Returns:
            对应的 _ChatQueue 实例
        """
        if chat_id not in self._queues:
            self._queues[chat_id] = _ChatQueue()
        return self._queues[chat_id]

    async def dispatch(self, chat_id: str, coro, on_start=None, on_done=None) -> None:
        """分发消息到指定聊天室队列。

        自动根据当前模式（QUEUE / PREEMPTIVE）决定处理方式。

        Args:
            chat_id: 目标聊天室标识
            coro: 要执行的协程
            on_start: 开始回调
            on_done: 完成回调
        """
        q = self._get_queue(chat_id)
        await q.enqueue(coro, self._mode, on_start, on_done)

    async def dispatch_cli(self, coro, on_start=None, on_done=None) -> None:
        """CLI 专用分发（使用内部 chat_id "__cli__"）。

        Args:
            coro: 要执行的协程
            on_start: 开始回调
            on_done: 完成回调
        """
        await self.dispatch(self.CLI_CHAT_ID, coro, on_start, on_done)

    def get_status(self) -> dict[str, Any]:
        """获取所有聊天室的队列状态。

        Returns:
            包含全局模式和每个聊天室状态的字典：
            {
                "mode": "queue" | "preemptive",
                "chats": {
                    "CLI": {"busy": bool, "pending": int, "elapsed": float},
                    "chat_123": {"busy": bool, "pending": int, "elapsed": float},
                }
            }
        """
        chats = {}
        for cid, q in self._queues.items():
            label = "CLI" if cid == self.CLI_CHAT_ID else cid
            chats[label] = {
                "busy": q.is_busy,
                "pending": q.pending,
                "elapsed": q.elapsed_seconds,
            }
        return {
            "mode": self._mode.value,
            "chats": chats,
        }

    def get_agent_status(self, chat_id: str | None = None) -> dict[str, Any]:
        """获取指定聊天室的 agent 状态（不中断执行）。

        Args:
            chat_id: 聊天室 ID，None 则返回全部

        Returns:
            agent 状态字典
        """
        if chat_id:
            q = self._get_queue(chat_id)
            return {
                "busy": q.is_busy,
                "pending": q.pending,
                "elapsed_seconds": round(q.elapsed_seconds, 1) if q.elapsed_seconds else None,
                "status": "processing" if q.is_busy else "idle",
            }

        # 返回全部
        return self.get_status()

    @staticmethod
    def interpret_status(status: dict) -> str:
        """将状态字典转换为人类可读的描述。

        Args:
            status: get_agent_status() 的返回值

        Returns:
            人类可读的状态描述
        """
        if not status.get("busy"):
            return "🟢 Agent 空闲"

        elapsed = status.get("elapsed_seconds")
        pending = status.get("pending", 0)
        lines = ["🔴 Agent 正在处理中"]
        if elapsed is not None:
            lines.append(f"  ⏱️ 已运行 {elapsed} 秒")
            if elapsed > 120:
                lines.append("  ⚠️ 运行时间较长，但可能仍在正常工作中")
        if pending > 0:
            lines.append(f"  📬 队列中还有 {pending} 条等待")
        return "\n".join(lines)


# MessageQueueManager 由 RuntimeContext.message_queue 持有（见 compat.unified_entry），非模块级单例。
