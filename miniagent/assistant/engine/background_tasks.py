"""Mini Agent Python — 后台任务管理器

支持在主session中启动子session并行执行任务，不污染主对话历史。

**生命周期**：
- 子 session 执行完成后，结果缓存在内存中供 ``/btw result`` 查询
- 子 session 的磁盘痕迹（工作区、记忆、日记、trace 等）在回合结束后自动清除

**性能优化**：
- TTL 自动清理：已完成任务默认 3600 秒后自动清理内存条目

**配置**：
- 并行上限受 ``agent.max_parallel_sessions`` 与常量 ``BACKGROUND_TASKS_MAX_CONCURRENT`` 约束
- TTL 默认见 ``BACKGROUND_TASKS_TASK_TTL_SECONDS``（constants.py）
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from miniagent.agent.constants import (
    BACKGROUND_TASKS_MAX_CONCURRENT,
    BACKGROUND_TASKS_TASK_TTL_SECONDS,
)
from miniagent.agent.logging import get_logger
from miniagent.assistant.contracts.messages import InboundMessage
from miniagent.assistant.engine.background_inbound import (
    background_prompt,
    build_background_inbound_message,
)
from miniagent.assistant.engine.bg_session_cleanup import cleanup_background_session_artifacts
from miniagent.assistant.infrastructure.json_config import get_config

_logger = get_logger(__name__)

DEFAULT_TASK_TTL_SECONDS = BACKGROUND_TASKS_TASK_TTL_SECONDS
DEFAULT_MAX_CONCURRENT = BACKGROUND_TASKS_MAX_CONCURRENT


class TaskStatus(str, Enum):
    """后台任务状态"""

    PENDING = "pending"  # 等待执行
    RUNNING = "running"  # 正在执行
    COMPLETED = "completed"  # 已完成
    FAILED = "failed"  # 失败
    CANCELLED = "cancelled"  # 已取消


@dataclass
class BackgroundTask:
    """后台任务条目"""

    task_id: str  # 任务唯一ID（UUID）
    session_key: str  # 子session标识（__bg__<uuid>）
    prompt: str  # 用户输入
    status: TaskStatus = TaskStatus.PENDING
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result: str | None = None  # 任务结果
    error: str | None = None  # 错误信息
    metadata: dict[str, Any] = field(default_factory=dict)  # 其他元数据


class BackgroundTaskManager:
    """后台任务管理器

    管理后台任务的生命周期：创建、执行、查询、取消、清理。

    Example:
        manager = BackgroundTaskManager()
        task_id = await manager.start_task(engine, "帮我分析这个文件", state)
        status = manager.get_status(task_id)
        result = await manager.get_result(task_id)
    """

    def __init__(
        self,
        max_concurrent: int | None = None,
        ttl_seconds: int | None = None,
    ) -> None:
        """创建后台任务管理器

        Args:
            max_concurrent: 最大并行任务数（None时从配置加载）
            ttl_seconds: 已完成任务 TTL（秒，None时从配置加载）
        """
        self._tasks: dict[str, BackgroundTask] = {}
        # 跟踪 in-flight 执行 task，防止 fire-and-forget 丢失异常（"Task exception never retrieved"）。
        self._exec_tasks: set[asyncio.Task] = set()
        self._exec_by_id: dict[str, asyncio.Task] = {}
        self._active_slots: set[str] = set()
        cfg_parallel_cap = int(get_config("agent.max_parallel_sessions", 4))
        base_max = max_concurrent if max_concurrent is not None else DEFAULT_MAX_CONCURRENT
        self._max_concurrent = max(1, min(base_max, cfg_parallel_cap))
        self._running_count = 0
        self._lock = asyncio.Lock()
        self._ttl_seconds = ttl_seconds if ttl_seconds is not None else DEFAULT_TASK_TTL_SECONDS
        self._cleanup_task: asyncio.Task | None = None
        self._closed = False
        self._start_cleanup_loop()

    def _ensure_cleanup_loop(self) -> None:
        """在有事件循环时启动 TTL 自动清理循环（幂等）。"""
        if self._closed:
            return
        if self._cleanup_task is not None and not self._cleanup_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._cleanup_task = loop.create_task(self._cleanup_loop())

    def _start_cleanup_loop(self) -> None:
        """启动 TTL 自动清理循环（构造时尽力启动；无 loop 时由 start_task 补启）。"""
        self._ensure_cleanup_loop()

    async def _cleanup_loop(self) -> None:
        """TTL 自动清理循环"""
        while True:
            try:
                await asyncio.sleep(60)  # 每 60 秒检查一次
                self._cleanup_expired_tasks()
            except asyncio.CancelledError:
                break
            except Exception:
                # 静默处理异常，避免循环中断
                pass

    def _cleanup_expired_tasks(self) -> int:
        """清理已过期任务（内部方法）

        Returns:
            清理的任务数量
        """
        now = datetime.now(timezone.utc)
        expired_keys = []
        for task_id, task in self._tasks.items():
            if task.completed_at and task.status in (
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            ):
                elapsed = (now - task.completed_at).total_seconds()
                if elapsed > self._ttl_seconds:
                    expired_keys.append(task_id)

        for key in expired_keys:
            del self._tasks[key]

        return len(expired_keys)

    async def start_task(
        self,
        engine: Any,
        prompt: str,
        state: Mapping[str, Any],
        **kwargs,
    ) -> str:
        """启动后台任务

        Args:
            engine: UnifiedEngine实例
            prompt: 用户输入
            state: 主session状态
            **kwargs: 其他参数传递给run_agent_with_thinking

        Returns:
            任务ID（用于后续查询）
        """
        if self._closed:
            raise RuntimeError("后台任务管理器已关闭")
        self._ensure_cleanup_loop()

        async with self._lock:
            if self._closed:
                raise RuntimeError("后台任务管理器已关闭")
            # 检查并行限制
            if self._running_count >= self._max_concurrent:
                raise RuntimeError(f"达到并行上限 {self._max_concurrent}，请等待其他任务完成")

            # 生成唯一ID
            task_id = str(uuid.uuid4())[:8]
            session_key = f"__bg__{task_id}"

            # 创建任务条目
            task = BackgroundTask(
                task_id=task_id,
                session_key=session_key,
                prompt=prompt,
                status=TaskStatus.PENDING,
            )
            self._tasks[task_id] = task
            # Reserve capacity before the new coroutine can be scheduled.
            # Counting only inside _execute_task allows concurrent start_task
            # calls to all pass the limit while every task is still pending.
            self._active_slots.add(task_id)
            self._running_count = len(self._active_slots)
            message = build_background_inbound_message(
                task_id,
                session_key,
                prompt,
                parent_session_key=(state.get("active_session_id") or None),
            )

            # 启动异步执行（跟踪 task 引用并在完成时记录异常，避免静默丢失）
            exec_task = asyncio.create_task(
                self._execute_task(engine, task, message, state, kwargs)
            )
            self._exec_tasks.add(exec_task)
            self._exec_by_id[task_id] = exec_task
            def _done(done: asyncio.Task[Any]) -> None:
                self._on_exec_task_done(task_id, done)

            exec_task.add_done_callback(_done)

            return task_id

    def _release_slot(self, task_id: str) -> None:
        """Idempotently release capacity on the event-loop thread."""
        self._active_slots.discard(task_id)
        self._running_count = len(self._active_slots)

    def _on_exec_task_done(self, task_id: str, task: asyncio.Task) -> None:
        """后台执行 task 完成回调：从跟踪集合移除并记录未捕获异常。"""
        self._exec_tasks.discard(task)
        if self._exec_by_id.get(task_id) is task:
            self._exec_by_id.pop(task_id, None)
        # A task cancelled before its coroutine first runs never reaches the
        # coroutine's finally block, so the callback is the final safety net.
        self._release_slot(task_id)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            _logger.error("后台任务执行异常: %s", exc, exc_info=exc)

    @staticmethod
    def _resolve_runtime_deps(state: Mapping[str, Any]) -> dict[str, Any]:
        """从 CLI 状态解析子 session 清理与引擎调用所需的运行时依赖。"""
        runtime_ctx = state.get("runtime_ctx")
        session_manager = state.get("session_manager")
        return {
            "session_manager": session_manager,
            "memory": getattr(runtime_ctx, "memory", None),
            "knowledge_registry": getattr(runtime_ctx, "knowledge_registry", None),
            "client": getattr(
                runtime_ctx,
                "llm_client",
                getattr(runtime_ctx, "llm_gateway", None),
            ),
        }

    async def _execute_task(
        self,
        engine: Any,
        task: BackgroundTask,
        message: InboundMessage,
        state: Mapping[str, Any],
        kwargs: dict[str, Any],
    ) -> None:
        """执行后台任务（内部方法）

        Args:
            engine: UnifiedEngine实例
            task: 任务条目
            message: 标准后台入站消息
            state: 主session状态
            kwargs: 其他参数
        """
        runtime_deps = self._resolve_runtime_deps(state)
        should_run = False
        try:
            async with self._lock:
                if task.status == TaskStatus.CANCELLED:
                    should_run = False
                else:
                    should_run = True
                    task.status = TaskStatus.RUNNING
                    task.started_at = datetime.now(timezone.utc)

            if should_run:
                try:
                    result = await engine.run_agent_with_thinking(
                        user_input=background_prompt(message),
                        session_key=message.session_key or task.session_key,
                        skill_toolboxes=state.get("skill_toolboxes", []),
                        skill_prompts=state.get("skill_prompts"),
                        is_feishu=False,
                        session_manager=runtime_deps["session_manager"],
                        memory=runtime_deps["memory"],
                        knowledge_registry=runtime_deps["knowledge_registry"],
                        client=runtime_deps["client"],
                        **kwargs,
                    )

                    async with self._lock:
                        if task.status != TaskStatus.CANCELLED:
                            task.status = TaskStatus.COMPLETED
                            task.completed_at = datetime.now(timezone.utc)
                            task.result = result

                except asyncio.CancelledError:
                    async with self._lock:
                        if task.status != TaskStatus.CANCELLED:
                            task.status = TaskStatus.CANCELLED
                            task.completed_at = datetime.now(timezone.utc)
                    raise

                except Exception as e:
                    async with self._lock:
                        if task.status != TaskStatus.CANCELLED:
                            task.status = TaskStatus.FAILED
                            task.completed_at = datetime.now(timezone.utc)
                            task.error = str(e)

        finally:
            self._exec_by_id.pop(task.task_id, None)
            async with self._lock:
                self._release_slot(task.task_id)
            try:
                await cleanup_background_session_artifacts(
                    task.session_key,
                    session_manager=runtime_deps["session_manager"],
                    memory=runtime_deps["memory"],
                )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                task.metadata["cleanup_error"] = type(error).__name__
                _logger.error(
                    "后台任务产物清理失败 (task=%s): %s",
                    task.task_id,
                    error,
                )

    def get_status(self, task_id: str) -> dict[str, Any] | None:
        """获取任务状态

        Args:
            task_id: 任务ID

        Returns:
            任务状态字典，或None（任务不存在）
        """
        task = self._tasks.get(task_id)
        if task is None:
            return None

        return {
            "task_id": task.task_id,
            "status": task.status.value,
            "prompt": task.prompt,
            "created_at": task.created_at.isoformat(),
            "started_at": task.started_at.isoformat() if task.started_at else None,
            "completed_at": task.completed_at.isoformat() if task.completed_at else None,
            "has_result": task.result is not None,
            "has_error": task.error is not None,
        }

    async def _wait_for_task_settled(self, task: BackgroundTask) -> None:
        """等待任务离开 pending/running（最多 30 秒）。"""
        if task.status in (TaskStatus.RUNNING, TaskStatus.PENDING):
            for _ in range(30):
                await asyncio.sleep(1)
                if task.status not in (TaskStatus.RUNNING, TaskStatus.PENDING):
                    break

    async def get_result(self, task_id: str) -> str | None:
        """获取任务成功结果

        Args:
            task_id: 任务ID

        Returns:
            成功时的结果文本；任务不存在、未完成、失败或已取消时返回 ``None``。
            失败详情请用 :meth:`get_error`。
        """
        task = self._tasks.get(task_id)
        if task is None:
            return None

        await self._wait_for_task_settled(task)
        return task.result

    async def get_error(self, task_id: str) -> str | None:
        """获取任务错误信息

        Args:
            task_id: 任务ID

        Returns:
            错误信息，或None（任务不存在、未完成或无错误）
        """
        task = self._tasks.get(task_id)
        if task is None:
            return None

        await self._wait_for_task_settled(task)
        return task.error

    async def cancel_task(self, task_id: str) -> bool:
        """取消任务（标记状态并 cancel 底层 asyncio.Task）

        若 Agent 已在运行，会在其下一个 ``await`` 点收到 ``CancelledError`` 并中止；
        子 session 磁盘痕迹在 :meth:`_execute_task` 的 ``finally`` 中清理。

        Args:
            task_id: 任务ID

        Returns:
            True 如果取消成功，False 如果任务不存在或已终态
        """
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False

            if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
                return False

            task.status = TaskStatus.CANCELLED
            task.completed_at = datetime.now(timezone.utc)

            exec_task = self._exec_by_id.get(task_id)
            if exec_task is not None and not exec_task.done():
                exec_task.cancel()

            return True

    def list_tasks(self) -> list[dict[str, Any]]:
        """列出所有任务

        Returns:
            任务状态列表
        """
        statuses = (self.get_status(task_id) for task_id in self._tasks)
        return [status for status in statuses if status is not None]

    def clear_completed(self) -> int:
        """清理已完成的任务

        Returns:
            清理的任务数量
        """
        count = 0
        for task_id, task in list(self._tasks.items()):
            if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
                del self._tasks[task_id]
                count += 1
        return count

    def get_stats(self) -> dict[str, Any]:
        """获取统计信息

        Returns:
            统计字典
        """
        status_counts = {
            status.value: sum(1 for t in self._tasks.values() if t.status == status)
            for status in TaskStatus
        }

        return {
            "total_tasks": len(self._tasks),
            "running_tasks": self._running_count,
            "max_concurrent": self._max_concurrent,
            "status_counts": status_counts,
        }

    async def shutdown(self) -> None:
        """Cancel and await the cleanup loop and every in-flight Agent task."""
        async with self._lock:
            self._closed = True
            completed_at = datetime.now(timezone.utc)
            for record in self._tasks.values():
                if record.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
                    record.status = TaskStatus.CANCELLED
                    record.completed_at = completed_at
            cleanup_task = self._cleanup_task
            exec_tasks = tuple(task for task in self._exec_tasks if not task.done())

        if cleanup_task is not None and not cleanup_task.done():
            cleanup_task.cancel()
        for exec_task in exec_tasks:
            exec_task.cancel()
        pending = tuple(
            pending_task
            for pending_task in (cleanup_task, *exec_tasks)
            if pending_task is not None
        )
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        self._cleanup_task = None
        self._exec_tasks.clear()
        self._exec_by_id.clear()
        self._active_slots.clear()
        self._running_count = 0


__all__ = [
    "BackgroundTaskManager",
    "BackgroundTask",
    "TaskStatus",
]
