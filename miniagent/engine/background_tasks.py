"""Mini Agent Python — 后台任务管理器

支持在主session中启动子session并行执行任务，不污染主对话历史。

**性能优化**：
- TTL 自动清理：已完成任务默认 3600 秒后自动清理

**配置**：
- 从JSON配置加载默认值，环境变量可覆盖
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from miniagent.infrastructure.json_config import get_config

# 从JSON配置加载默认值，环境变量可覆盖
DEFAULT_TASK_TTL_SECONDS = get_config("background_tasks.task_ttl_seconds", 3600)
DEFAULT_MAX_CONCURRENT = get_config("background_tasks.max_concurrent", 4)

# 环境变量覆盖支持
import os
if os.environ.get("MINIAGENT_TASK_TTL_SECONDS"):
    try:
        DEFAULT_TASK_TTL_SECONDS = int(os.environ.get("MINIAGENT_TASK_TTL_SECONDS") or str(DEFAULT_TASK_TTL_SECONDS))
    except ValueError:
        pass
if os.environ.get("MINIAGENT_MAX_CONCURRENT"):
    try:
        DEFAULT_MAX_CONCURRENT = int(os.environ.get("MINIAGENT_MAX_CONCURRENT") or str(DEFAULT_MAX_CONCURRENT))
    except ValueError:
        pass


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
        self._max_concurrent = max_concurrent if max_concurrent is not None else DEFAULT_MAX_CONCURRENT
        self._running_count = 0
        self._lock = asyncio.Lock()
        self._ttl_seconds = ttl_seconds if ttl_seconds is not None else DEFAULT_TASK_TTL_SECONDS
        self._cleanup_task: asyncio.Task | None = None
        # 启动自动清理任务
        self._start_cleanup_loop()

    def _start_cleanup_loop(self) -> None:
        """启动 TTL 自动清理循环（后台任务）"""
        try:
            loop = asyncio.get_running_loop()
            self._cleanup_task = loop.create_task(self._cleanup_loop())
        except RuntimeError:
            # 没有运行的事件循环，稍后在首次使用时启动
            pass

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
        state: dict[str, Any],
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
        async with self._lock:
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

            # 启动异步执行
            asyncio.create_task(self._execute_task(engine, task, state, kwargs))

            return task_id

    async def _execute_task(
        self,
        engine: Any,
        task: BackgroundTask,
        state: dict[str, Any],
        kwargs: dict[str, Any],
    ) -> None:
        """执行后台任务（内部方法）

        Args:
            engine: UnifiedEngine实例
            task: 任务条目
            state: 主session状态
            kwargs: 其他参数
        """
        async with self._lock:
            self._running_count += 1
            task.status = TaskStatus.RUNNING
            task.started_at = datetime.now(timezone.utc)

        try:
            # 执行Agent
            result = await engine.run_agent_with_thinking(
                user_input=task.prompt,
                session_key=task.session_key,
                skill_toolboxes=state.get("skill_toolboxes", []),
                skill_prompts=state.get("skill_prompts"),
                is_feishu=False,
                **kwargs,
            )

            # 保存结果
            async with self._lock:
                task.status = TaskStatus.COMPLETED
                task.completed_at = datetime.now(timezone.utc)
                task.result = result
                self._running_count -= 1

        except Exception as e:
            # 记录错误
            async with self._lock:
                task.status = TaskStatus.FAILED
                task.completed_at = datetime.now(timezone.utc)
                task.error = str(e)
                self._running_count -= 1

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

    async def get_result(self, task_id: str) -> str | None:
        """获取任务结果

        Args:
            task_id: 任务ID

        Returns:
            任务结果，或None（任务不存在或未完成）
        """
        task = self._tasks.get(task_id)
        if task is None:
            return None

        # 等待任务完成（最多30秒）
        if task.status == TaskStatus.RUNNING:
            for _ in range(30):
                await asyncio.sleep(1)
                if task.status != TaskStatus.RUNNING:
                    break

        return task.result

    async def cancel_task(self, task_id: str) -> bool:
        """取消任务

        Args:
            task_id: 任务ID

        Returns:
            True如果取消成功，False如果任务不存在或已完成
        """
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False

            if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
                return False

            # 保存原状态，因为修改后无法检查
            was_running = task.status == TaskStatus.RUNNING
            task.status = TaskStatus.CANCELLED
            task.completed_at = datetime.now(timezone.utc)
            if was_running:
                self._running_count -= 1

            return True

    def list_tasks(self) -> list[dict[str, Any]]:
        """列出所有任务

        Returns:
            任务状态列表
        """
        return [self.get_status(task_id) for task_id in self._tasks.keys()]

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
        status_counts = {}
        for status in TaskStatus:
            status_counts[status.value] = sum(
                1 for t in self._tasks.values() if t.status == status
            )

        return {
            "total_tasks": len(self._tasks),
            "running_tasks": self._running_count,
            "max_concurrent": self._max_concurrent,
            "status_counts": status_counts,
        }


__all__ = [
    "BackgroundTaskManager",
    "BackgroundTask",
    "TaskStatus",
]