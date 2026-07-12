"""Tests for miniagent/engine/background_tasks.py."""

import asyncio
from datetime import timezone

import pytest

from miniagent.engine.background_inbound import build_background_inbound_message
from miniagent.engine.background_tasks import (
    BackgroundTask,
    BackgroundTaskManager,
    TaskStatus,
)


class TestBackgroundTask:
    """Tests for BackgroundTask dataclass."""

    def test_task_creation(self):
        """Basic task creation."""
        task = BackgroundTask(
            task_id="test123",
            session_key="__bg__test123",
            prompt="Test prompt",
        )
        assert task.task_id == "test123"
        assert task.session_key == "__bg__test123"
        assert task.prompt == "Test prompt"
        assert task.status == TaskStatus.PENDING
        assert task.result is None
        assert task.error is None

    def test_task_with_metadata(self):
        """Task with custom metadata."""
        task = BackgroundTask(
            task_id="meta456",
            session_key="__bg__meta456",
            prompt="Meta test",
            metadata={"custom": "value"},
        )
        assert task.metadata == {"custom": "value"}

    def test_task_timestamps(self):
        """Task timestamps are timezone-aware."""
        task = BackgroundTask(
            task_id="time789",
            session_key="__bg__time789",
            prompt="Time test",
        )
        assert task.created_at.tzinfo == timezone.utc
        assert task.started_at is None
        assert task.completed_at is None


class TestTaskStatus:
    """Tests for TaskStatus enum."""

    def test_status_values(self):
        """Status enum has expected values."""
        assert TaskStatus.PENDING.value == "pending"
        assert TaskStatus.RUNNING.value == "running"
        assert TaskStatus.COMPLETED.value == "completed"
        assert TaskStatus.FAILED.value == "failed"
        assert TaskStatus.CANCELLED.value == "cancelled"

    def test_status_string_conversion(self):
        """Status value can be converted to string."""
        assert TaskStatus.RUNNING.value == "running"
        assert TaskStatus.COMPLETED.value == "completed"


class TestBackgroundTaskManager:
    """Tests for BackgroundTaskManager."""

    def test_manager_creation(self):
        """Manager creation with default concurrency."""
        manager = BackgroundTaskManager()
        assert manager._max_concurrent == 4
        assert manager._running_count == 0
        assert len(manager._tasks) == 0

    def test_manager_creation_custom_concurrency(self):
        """显式 max_concurrent 仍受 agent.max_parallel_sessions 上限约束。"""
        manager = BackgroundTaskManager(max_concurrent=8)
        assert manager._max_concurrent == 4

    def test_get_status_nonexistent(self):
        """get_status returns None for nonexistent task."""
        manager = BackgroundTaskManager()
        status = manager.get_status("nonexistent")
        assert status is None

    def test_list_tasks_empty(self):
        """list_tasks returns empty list when no tasks."""
        manager = BackgroundTaskManager()
        tasks = manager.list_tasks()
        assert tasks == []

    def test_clear_completed_empty(self):
        """clear_completed returns 0 when no tasks."""
        manager = BackgroundTaskManager()
        count = manager.clear_completed()
        assert count == 0

    def test_get_stats_empty(self):
        """get_stats returns correct stats for empty manager."""
        manager = BackgroundTaskManager()
        stats = manager.get_stats()
        assert stats["total_tasks"] == 0
        assert stats["running_tasks"] == 0
        assert stats["max_concurrent"] == 4


class TestBackgroundTaskManagerAsync:
    """Async tests for BackgroundTaskManager."""

    @pytest.mark.asyncio
    async def test_start_task_concurrent_limit(self):
        """start_task raises error when concurrent limit reached."""
        manager = BackgroundTaskManager(max_concurrent=1)

        # Create a mock engine that doesn't immediately complete
        class MockEngine:
            async def run_agent_with_thinking(self, **kwargs):
                await asyncio.sleep(10)  # Long-running task

        engine = MockEngine()
        state = {"skill_toolboxes": [], "skill_prompts": None}

        # Start first task
        task_id1 = await manager.start_task(engine, "Task 1", state)
        assert task_id1 is not None

        # Wait for task to start running (running_count to increment)
        await asyncio.sleep(0.1)

        # Now try to start second task - should raise
        with pytest.raises(RuntimeError):
            await manager.start_task(engine, "Task 2", state)

    @pytest.mark.asyncio
    async def test_cancel_task_nonexistent(self):
        """cancel_task returns False for nonexistent task."""
        manager = BackgroundTaskManager()
        result = await manager.cancel_task("nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_get_result_nonexistent(self):
        """get_result returns None for nonexistent task."""
        manager = BackgroundTaskManager()
        result = await manager.get_result("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_task_lifecycle_quick(self):
        """Test quick task lifecycle with mock engine."""
        manager = BackgroundTaskManager()

        class MockEngine:
            async def run_agent_with_thinking(self, **kwargs):
                await asyncio.sleep(0.1)
                return "Mock result"

        engine = MockEngine()
        state = {"skill_toolboxes": [], "skill_prompts": None}

        # Start task
        task_id = await manager.start_task(engine, "Quick test", state)

        # Wait for completion
        await asyncio.sleep(0.2)

        # Check status
        status = manager.get_status(task_id)
        assert status is not None
        assert status["status"] == "completed"
        assert status["has_result"] is True

        # Get result
        result = await manager.get_result(task_id)
        assert result == "Mock result"

    @pytest.mark.asyncio
    async def test_get_error_failed_task(self):
        """get_error returns error message for failed tasks."""
        manager = BackgroundTaskManager()

        class FailingEngine:
            async def run_agent_with_thinking(self, **kwargs):
                raise RuntimeError("boom")

        engine = FailingEngine()
        state = {"skill_toolboxes": [], "skill_prompts": None}

        task_id = await manager.start_task(engine, "Fail task", state)
        await asyncio.sleep(0.2)

        error = await manager.get_error(task_id)
        assert error == "boom"

    @pytest.mark.asyncio
    async def test_task_failure_lifecycle(self):
        """Failed task has failed status and no result."""
        manager = BackgroundTaskManager()

        class FailingEngine:
            async def run_agent_with_thinking(self, **kwargs):
                raise ValueError("task failed")

        engine = FailingEngine()
        state = {"skill_toolboxes": [], "skill_prompts": None}

        task_id = await manager.start_task(engine, "Fail", state)
        await asyncio.sleep(0.2)

        status = manager.get_status(task_id)
        assert status["status"] == "failed"
        assert status["has_error"] is True
        assert status["has_result"] is False
        assert await manager.get_result(task_id) is None

    @pytest.mark.asyncio
    async def test_clear_completed_with_tasks(self):
        """clear_completed removes completed tasks."""
        manager = BackgroundTaskManager()

        class MockEngine:
            async def run_agent_with_thinking(self, **kwargs):
                return "Result"

        engine = MockEngine()
        state = {"skill_toolboxes": [], "skill_prompts": None}

        # Start and complete a task
        task_id = await manager.start_task(engine, "Test", state)
        await asyncio.sleep(0.2)

        # Clear completed
        count = manager.clear_completed()
        assert count == 1

        # Verify task removed
        status = manager.get_status(task_id)
        assert status is None


class TestBackgroundTaskManagerCancel:
    """Tests for task cancellation."""

    @pytest.mark.asyncio
    async def test_cancel_running_task(self):
        """Cancel a running task and keep cancelled status after execution ends."""
        manager = BackgroundTaskManager()

        class SlowEngine:
            async def run_agent_with_thinking(self, **kwargs):
                await asyncio.sleep(10)

        engine = SlowEngine()
        state = {"skill_toolboxes": [], "skill_prompts": None}

        task_id = await manager.start_task(engine, "Slow task", state)
        await asyncio.sleep(0.05)

        result = await manager.cancel_task(task_id)
        assert result is True

        await asyncio.sleep(0.2)

        status = manager.get_status(task_id)
        assert status["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_pending_task(self):
        """Cancel a pending task before execution transitions to running."""
        manager = BackgroundTaskManager()

        task = BackgroundTask(
            task_id="pending01",
            session_key="__bg__pending01",
            prompt="Pending",
            status=TaskStatus.PENDING,
        )
        manager._tasks["pending01"] = task

        class SlowEngine:
            async def run_agent_with_thinking(self, **kwargs):
                await asyncio.sleep(10)

        state = {"skill_toolboxes": [], "skill_prompts": None}
        message = build_background_inbound_message(
            task.task_id,
            task.session_key,
            task.prompt,
        )
        exec_task = asyncio.create_task(
            manager._execute_task(SlowEngine(), task, message, state, {})
        )
        manager._exec_by_id["pending01"] = exec_task

        result = await manager.cancel_task("pending01")
        assert result is True
        assert manager.get_status("pending01")["status"] == "cancelled"

        try:
            await exec_task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_cancel_completed_task_fails(self):
        """Cannot cancel a completed task."""
        manager = BackgroundTaskManager()

        class FastEngine:
            async def run_agent_with_thinking(self, **kwargs):
                return "Done"

        engine = FastEngine()
        state = {"skill_toolboxes": [], "skill_prompts": None}

        task_id = await manager.start_task(engine, "Fast task", state)
        await asyncio.sleep(0.2)

        # Try to cancel completed task
        result = await manager.cancel_task(task_id)
        assert result is False


class TestBackgroundTaskManagerStats:
    """Tests for statistics tracking."""

    @pytest.mark.asyncio
    async def test_stats_with_tasks(self):
        """get_stats returns correct stats with tasks."""
        manager = BackgroundTaskManager(max_concurrent=2)

        class SlowEngine:
            async def run_agent_with_thinking(self, **kwargs):
                await asyncio.sleep(1)

        engine = SlowEngine()
        state = {"skill_toolboxes": [], "skill_prompts": None}

        # Start two tasks
        await manager.start_task(engine, "Task 1", state)
        await manager.start_task(engine, "Task 2", state)

        # Wait for tasks to start running
        await asyncio.sleep(0.1)

        stats = manager.get_stats()
        assert stats["total_tasks"] == 2
        assert stats["running_tasks"] == 2
        assert stats["max_concurrent"] == 2

    def test_cleanup_loop_deferred_without_running_loop(self):
        """无事件循环时构造管理器不会启动 TTL 循环。"""
        manager = BackgroundTaskManager()
        assert manager._cleanup_task is None

    @pytest.mark.asyncio
    async def test_cleanup_loop_starts_on_start_task(self):
        """首次 start_task 时补启 TTL 清理循环。"""
        manager = BackgroundTaskManager()
        manager._cleanup_task = None

        class FastEngine:
            async def run_agent_with_thinking(self, **kwargs):
                return "ok"

        await manager.start_task(
            FastEngine(), "ttl", {"skill_toolboxes": [], "skill_prompts": None}
        )
        assert manager._cleanup_task is not None
        assert not manager._cleanup_task.done()

    @pytest.mark.asyncio
    async def test_running_count_not_negative_after_cancel(self):
        """取消运行中任务后 running_count 不应为负。"""
        manager = BackgroundTaskManager(max_concurrent=2)

        class SlowEngine:
            async def run_agent_with_thinking(self, **kwargs):
                await asyncio.sleep(10)

        engine = SlowEngine()
        state = {"skill_toolboxes": [], "skill_prompts": None}

        task_id = await manager.start_task(engine, "slow", state)
        await asyncio.sleep(0.05)
        await manager.cancel_task(task_id)
        await asyncio.sleep(0.2)

        assert manager._running_count == 0
        assert manager.get_stats()["running_tasks"] == 0


class TestBackgroundTaskManagerShutdown:
    """Process shutdown cancels internal maintenance and execution tasks."""

    @pytest.mark.asyncio
    async def test_shutdown_awaits_execution_cleanup(self, monkeypatch):
        manager = BackgroundTaskManager()
        cleanup_finished = asyncio.Event()

        async def cleanup(*_args, **_kwargs):
            cleanup_finished.set()

        monkeypatch.setattr(
            "miniagent.engine.background_tasks.cleanup_background_session_artifacts",
            cleanup,
        )

        class SlowEngine:
            async def run_agent_with_thinking(self, **_kwargs):
                await asyncio.Event().wait()

        task_id = await manager.start_task(
            SlowEngine(),
            "shutdown",
            {"skill_toolboxes": [], "skill_prompts": None},
        )
        await asyncio.sleep(0)
        cleanup_task = manager._cleanup_task
        exec_task = manager._exec_by_id[task_id]

        await manager.shutdown()
        await manager.shutdown()

        assert cleanup_task is not None and cleanup_task.done()
        assert exec_task.done()
        assert cleanup_finished.is_set()
        assert manager.get_status(task_id)["status"] == "cancelled"
        assert manager._cleanup_task is None
        assert manager._exec_tasks == set()

    @pytest.mark.asyncio
    async def test_closed_manager_rejects_new_tasks(self):
        manager = BackgroundTaskManager()
        await manager.shutdown()

        with pytest.raises(RuntimeError, match="已关闭"):
            await manager.start_task(
                object(),
                "late",
                {"skill_toolboxes": [], "skill_prompts": None},
            )
