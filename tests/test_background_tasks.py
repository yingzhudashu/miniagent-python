"""Tests for miniagent/engine/background_tasks.py."""

import asyncio
from datetime import timezone

import pytest

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
        """Manager creation with custom concurrency."""
        manager = BackgroundTaskManager(max_concurrent=8)
        assert manager._max_concurrent == 8

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
        """Cancel a running task."""
        manager = BackgroundTaskManager()

        class SlowEngine:
            async def run_agent_with_thinking(self, **kwargs):
                await asyncio.sleep(10)  # Will be cancelled

        engine = SlowEngine()
        state = {"skill_toolboxes": [], "skill_prompts": None}

        task_id = await manager.start_task(engine, "Slow task", state)

        # Cancel immediately
        result = await manager.cancel_task(task_id)
        assert result is True

        # Check status
        status = manager.get_status(task_id)
        assert status["status"] == "cancelled"

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