"""多消息队列管理器（CLI / 飞书共享）

支持两种模式：

- **queue**：默认；同一 ``chat_id`` 内消息严格串行，先入先出。
- **preemptive**：新消息取消当前协程并清空等待队列，适用于「只要最新指令」的交互。

每个聊天室独立一条逻辑队列；CLI 固定使用 ``chat_id="__cli__"``。与引擎主循环的衔接见
``miniagent.engine.main``；架构背景见 ``docs/ARCHITECTURE.md``（消息队列与双通道）。

**与 ChannelRouter 的关系**：飞书入站经 :class:`miniagent.infrastructure.channel_router.ChannelRouter`
解析 ``session_key`` 后，应将「跑一轮 Agent」的协程投递到本管理器的 ``enqueue``，由 ``chat_id``（或
路由键）保证同一聊天室内顺序或抢占语义；切勿在路由层再开无队列保护的并行 ``create_task`` 调用引擎，
否则可能打乱会话历史与锁语义。
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

    def __init__(self, manager: MessageQueueManager | None = None) -> None:
        """初始化聊天室队列。

        属性说明：
        - _lock: asyncio 互斥锁，保证同一聊天室消息串行处理
        - _processing: 是否正在处理消息
        - _queue: 排队等待处理的任务列表（仅队列模式使用）
        - _current_task: 当前正在执行的任务
        - _task_start_time: 当前任务开始时间（用于计算运行时长）
        - _dispatch_wait_tasks: ``dispatch_wait`` 创建的包装 Task（不在 ``_queue`` 中），供 ``abort_pending`` 一并取消
        - _manager: 指向全局 MessageQueueManager，用于获取跨队列执行锁
        """
        self._lock = asyncio.Lock()
        self._processing = False
        self._queue: list[asyncio.Task] = []
        self._current_task: asyncio.Task | None = None
        self._task_start_time: float | None = None
        self._dispatch_wait_tasks: set[asyncio.Task] = set()
        self._manager = manager

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
            # 直接执行新任务（不走队列），但仍需获取全局执行锁
            if self._manager is not None and self._manager.exec_lock is not None:
                await self._manager.exec_lock.acquire()
            try:
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
            finally:
                if self._manager is not None and self._manager.exec_lock is not None:
                    self._manager.exec_lock.release()
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

        **跨队列排序**：在持有本队列锁的同时获取全局执行锁，
        防止其他队列的任务抢先执行，确保全局 FIFO。

        Args:
            coro: 要执行的协程
            on_start: 开始回调
            on_done: 完成回调
        """
        user_coro_started = False
        try:
            async with self._lock:
                self._processing = True
                self.mark_task_start()
                # 跨队列执行锁：在持有队列锁时获取，保证全局 FIFO
                if self._manager is not None and self._manager.exec_lock is not None:
                    await self._manager.exec_lock.acquire()
                try:
                    if on_start:
                        on_start()
                    user_coro_started = True
                    await coro
                    if on_done:
                        on_done()
                finally:
                    if self._manager is not None and self._manager.exec_lock is not None:
                        self._manager.exec_lock.release()
                    self._processing = False
                    self.mark_task_end()
        except asyncio.CancelledError:
            if not user_coro_started and asyncio.iscoroutine(coro):
                coro.close()
            raise

    def register_dispatch_wait_task(self, task: asyncio.Task) -> None:
        """由 ``MessageQueueManager.dispatch_wait``（QUEUE 模式）登记，结束时须 ``unregister_dispatch_wait_task``。"""
        self._dispatch_wait_tasks.add(task)

    def unregister_dispatch_wait_task(self, task: asyncio.Task) -> None:
        """从 ``dispatch_wait`` 跟踪集合移除已完成/已取消的任务句柄。"""
        self._dispatch_wait_tasks.discard(task)

    def abort_pending(self, mode: QueueMode) -> dict[str, Any]:
        """取消本聊天室队列上的工作：当前执行中的包装任务 + 排队中的任务。

        不退出进程、不注销实例。用于 `.queue abort` / 飞书打断。

        Args:
            mode: 全局队列模式（PREEMPTIVE 时需取消 ``_current_task``）。

        Returns:
            ``cancelled_running``：是否取消了正在执行的主任务；
            ``cancelled_pending``：取消的仅排队（等待锁）的包装任务数；
            ``cancelled_preemptive_current``：是否取消了 preemptive 路径上的当前任务。
            ``cancelled_dispatch_wait``：取消的 ``dispatch_wait`` 包装任务数（QUEUE 模式定时任务等）。
        """
        cancelled_preemptive_current = False
        if mode == QueueMode.PREEMPTIVE:
            for t in list(self._queue):
                if not t.done():
                    t.cancel()
            self._queue.clear()
            wait_cancelled_pre = 0
            for t in list(self._dispatch_wait_tasks):
                if not t.done():
                    t.cancel()
                    wait_cancelled_pre += 1
            self._dispatch_wait_tasks.clear()
            if self._current_task is not None and not self._current_task.done():
                self._current_task.cancel()
                cancelled_preemptive_current = True
            return {
                "cancelled_running": cancelled_preemptive_current,
                "cancelled_pending": 0,
                "cancelled_preemptive_current": cancelled_preemptive_current,
                "cancelled_dispatch_wait": wait_cancelled_pre,
            }

        was_processing = self._processing
        cancelled_wrappers = 0
        for t in list(self._queue):
            if not t.done():
                t.cancel()
                cancelled_wrappers += 1
        self._queue = [t for t in self._queue if not t.done()]

        wait_cancelled = 0
        for t in list(self._dispatch_wait_tasks):
            if not t.done():
                t.cancel()
                wait_cancelled += 1
        self._dispatch_wait_tasks = {t for t in self._dispatch_wait_tasks if not t.done()}

        total_wrappers = cancelled_wrappers + wait_cancelled
        cancelled_running = 1 if (was_processing and total_wrappers >= 1) else 0
        cancelled_pending = max(0, total_wrappers - cancelled_running)
        return {
            "cancelled_running": bool(cancelled_running),
            "cancelled_pending": cancelled_pending,
            "cancelled_preemptive_current": False,
            "cancelled_dispatch_wait": wait_cancelled,
        }


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
        # 跨队列执行排序锁：各 _ChatQueue._run_sequential 在运行协程前必须获取此锁，
        # 保证 CLI 与飞书等不同通道的消息按入队顺序全局 FIFO 执行。
        # 注意：此锁与 engine._exec_lock **不同**，避免同一任务重复获取同一 asyncio.Lock 导致死锁。
        self.exec_lock: asyncio.Lock | None = None

    def ensure_exec_lock(self) -> asyncio.Lock:
        """获取或创建跨队列执行排序锁。

        若尚未设置，自动创建一个新的 ``asyncio.Lock``。
        返回当前使用的锁实例。
        """
        if self.exec_lock is None:
            self.exec_lock = asyncio.Lock()
        return self.exec_lock

    @property
    def mode(self) -> QueueMode:
        """当前消息处理模式。

        Returns:
            QUEUE（顺序处理）或 PREEMPTIVE（打断模式）。
        """
        return self._mode

    @mode.setter
    def mode(self, value: QueueMode) -> None:
        """设置队列模式（顺序或抢占）；不中断已在跑的任务。"""
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
            self._queues[chat_id] = _ChatQueue(manager=self)
        return self._queues[chat_id]

    def set_exec_lock(self, lock: asyncio.Lock) -> None:
        """设置跨队列执行排序锁。

        各队列的 ``_run_sequential`` 在运行协程前必须先获取此锁，
        从而保证跨队列的 FIFO 执行顺序（CLI 与飞书消息的全局排序）。

        **注意**：此锁**不能**是 ``engine._exec_lock``（同一任务重复获取同一
        ``asyncio.Lock`` 会死锁）。应使用 ``MessageQueueManager.ensure_exec_lock()``
        创建独立的锁实例。

        Args:
            lock: asyncio.Lock 实例
        """
        self.exec_lock = lock

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

    async def dispatch_wait(self, chat_id: str, coro, on_start=None, on_done=None) -> None:
        """与 :meth:`dispatch` 同一串行锁，但阻塞直到 ``coro`` 执行完毕（QUEUE 模式）。

        供定时任务等需在触发点确认落盘后再继续的逻辑；普通消息仍用 ``dispatch``。
        """
        q = self._get_queue(chat_id)
        if self._mode == QueueMode.PREEMPTIVE:
            await q.enqueue(coro, self._mode, on_start, on_done)
            return
        t = asyncio.create_task(q._run_sequential(coro, on_start, on_done))
        q.register_dispatch_wait_task(t)
        try:
            await t
        finally:
            q.unregister_dispatch_wait_task(t)

    async def dispatch_cli(self, coro, on_start=None, on_done=None) -> None:
        """CLI 专用分发（使用内部 chat_id "__cli__"）。

        Args:
            coro: 要执行的协程
            on_start: 开始回调
            on_done: 完成回调
        """
        await self.dispatch(self.CLI_CHAT_ID, coro, on_start, on_done)

    async def dispatch_cli_wait(self, coro, on_start=None, on_done=None) -> None:
        """CLI 队列上分发并等待 ``coro`` 完成（见 :meth:`dispatch_wait`）。"""
        await self.dispatch_wait(self.CLI_CHAT_ID, coro, on_start, on_done)

    def abort_chat(self, chat_id: str) -> dict[str, Any]:
        """中止指定 ``chat_id`` 队列：取消运行中与排队中的任务，不退出进程。

        懒创建：若该聊天室尚无队列，返回零计数。
        """
        if chat_id not in self._queues:
            return {
                "chat_id": chat_id,
                "cancelled_running": False,
                "cancelled_pending": 0,
                "cancelled_preemptive_current": False,
                "cancelled_dispatch_wait": 0,
            }
        q = self._queues[chat_id]
        out = q.abort_pending(self._mode)
        out["chat_id"] = chat_id
        return out

    def abort_all_chats(self) -> dict[str, Any]:
        """对所有已创建的聊天室队列调用 :meth:`abort_chat`（进程退出时用）。"""
        merged: dict[str, Any] = {"chats": {}}
        for cid in list(self._queues.keys()):
            merged["chats"][cid] = self.abort_chat(cid)
        return merged

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


# MessageQueueManager 由 RuntimeContext.message_queue 持有，非模块级单例。
