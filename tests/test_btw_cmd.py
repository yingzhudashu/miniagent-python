"""Tests for miniagent/engine/btw_cmd.py."""

import pytest

from miniagent.engine.btw_cmd import (
    cmd_btw_clear,
    cmd_btw_status,
    get_background_task_manager,
)
from miniagent.types.error_prefix import ERROR_PREFIX, SUCCESS_PREFIX, WARNING_PREFIX


class TestGetBackgroundTaskManager:
    """Tests for singleton manager retrieval."""

    def test_singleton_returns_manager(self):
        """get_background_task_manager returns a manager."""
        manager = get_background_task_manager()
        assert manager is not None
        assert hasattr(manager, "start_task")
        assert hasattr(manager, "get_status")

    def test_singleton_is_same_instance(self):
        """Multiple calls return same instance."""
        manager1 = get_background_task_manager()
        manager2 = get_background_task_manager()
        assert manager1 is manager2


class TestCmdBtwStatus:
    """Tests for cmd_btw_status function."""

    def test_status_nonexistent_task(self):
        """cmd_btw_status returns error for nonexistent task."""
        result = cmd_btw_status("nonexistent123")
        assert "不存在" in result
        assert ERROR_PREFIX in result

    def test_status_empty_list(self):
        """cmd_btw_status without ID shows empty list message."""
        # Clear any existing tasks first
        cmd_btw_clear()

        result = cmd_btw_status()
        assert "没有后台任务" in result or "任务列表" in result

    def test_status_with_id_format(self):
        """cmd_btw_status with ID returns formatted output."""
        result = cmd_btw_status("nonexistent")
        # Should contain markdown-like formatting
        assert "##" in result or "任务" in result


class TestCmdBtwClear:
    """Tests for cmd_btw_clear function."""

    def test_clear_empty(self):
        """cmd_btw_clear returns appropriate message when nothing to clear."""
        # Clear first to ensure empty state
        manager = get_background_task_manager()
        manager._tasks.clear()

        result = cmd_btw_clear()
        assert "没有需要清理" in result or "已清理" in result

    def test_clear_returns_string(self):
        """cmd_btw_clear returns a string result."""
        result = cmd_btw_clear()
        assert isinstance(result, str)


class TestCmdBtwStart:
    """Tests for cmd_btw_start async function."""

    @pytest.mark.asyncio
    async def test_start_with_mock_engine(self):
        """cmd_btw_start with mock engine returns task ID."""
        from miniagent.engine.btw_cmd import cmd_btw_start

        class MockEngine:
            async def run_agent_with_thinking(self, **kwargs):
                return "Mock result"

        engine = MockEngine()
        state = {"skill_toolboxes": [], "skill_prompts": None}

        result = await cmd_btw_start(engine, "Test prompt", state)

        # Should contain task ID and success message
        assert SUCCESS_PREFIX in result
        assert "已启动" in result
        assert "Test prompt" in result

    @pytest.mark.asyncio
    async def test_start_short_prompt_no_ellipsis(self):
        """Short prompts are not truncated with ellipsis."""
        from miniagent.engine.btw_cmd import cmd_btw_start

        class MockEngine:
            async def run_agent_with_thinking(self, **kwargs):
                return "ok"

        result = await cmd_btw_start(MockEngine(), "hi", {"skill_toolboxes": []})
        assert "输入: hi" in result
        assert "hi..." not in result

    @pytest.mark.asyncio
    async def test_start_at_concurrent_limit(self):
        """cmd_btw_start fails when concurrent limit reached."""
        import asyncio

        from miniagent.engine.btw_cmd import cmd_btw_start, get_background_task_manager

        manager = get_background_task_manager()
        original_max = manager._max_concurrent
        manager._max_concurrent = 1
        manager._running_count = 1  # Simulate running task

        class MockEngine:
            async def run_agent_with_thinking(self, **kwargs):
                await asyncio.sleep(10)

        engine = MockEngine()
        state = {"skill_toolboxes": [], "skill_prompts": None}

        result = await cmd_btw_start(engine, "Should fail", state)

        # Should contain warning or error
        assert WARNING_PREFIX in result or "并行上限" in result

        # Restore
        manager._max_concurrent = original_max
        manager._running_count = 0


class TestCmdBtwResult:
    """Tests for cmd_btw_result async function."""

    @pytest.mark.asyncio
    async def test_result_failed_task(self):
        """cmd_btw_result returns error for failed tasks."""
        import asyncio

        from miniagent.engine.btw_cmd import cmd_btw_result, cmd_btw_start

        class FailingEngine:
            async def run_agent_with_thinking(self, **kwargs):
                raise RuntimeError("expected failure")

        engine = FailingEngine()
        state = {"skill_toolboxes": [], "skill_prompts": None}

        start_result = await cmd_btw_start(engine, "fail me", state)
        import re
        match = re.search(r"已启动: ([a-f0-9]+)", start_result)
        task_id = match.group(1)

        await asyncio.sleep(0.2)

        result = await cmd_btw_result(task_id)
        assert "错误" in result
        assert "expected failure" in result

    @pytest.mark.asyncio
    async def test_result_nonexistent_task(self):
        """cmd_btw_result returns error for nonexistent task."""
        from miniagent.engine.btw_cmd import cmd_btw_result

        result = await cmd_btw_result("nonexistent")
        assert "不存在" in result
        assert ERROR_PREFIX in result


class TestCmdBtwCancel:
    """Tests for cmd_btw_cancel async function."""

    @pytest.mark.asyncio
    async def test_cancel_running_task_stays_cancelled(self):
        """Cancelled running task remains cancelled after asyncio task ends."""
        import asyncio

        from miniagent.engine.btw_cmd import cmd_btw_cancel, cmd_btw_start, cmd_btw_status

        class SlowEngine:
            async def run_agent_with_thinking(self, **kwargs):
                await asyncio.sleep(10)

        engine = SlowEngine()
        state = {"skill_toolboxes": [], "skill_prompts": None}

        start_result = await cmd_btw_start(engine, "slow", state)
        import re
        match = re.search(r"已启动: ([a-f0-9]+)", start_result)
        task_id = match.group(1)

        await asyncio.sleep(0.05)
        cancel_result = await cmd_btw_cancel(task_id)
        assert "已取消" in cancel_result

        await asyncio.sleep(0.2)
        status_result = cmd_btw_status(task_id)
        assert "cancelled" in status_result

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_task(self):
        """cmd_btw_cancel returns error for nonexistent task."""
        from miniagent.engine.btw_cmd import cmd_btw_cancel

        result = await cmd_btw_cancel("nonexistent")
        assert "不存在" in result
        assert ERROR_PREFIX in result


class TestIntegration:
    """Integration tests for btw command workflow."""

    @pytest.mark.asyncio
    async def test_full_workflow(self):
        """Test full btw workflow: start -> status -> result -> clear."""
        import asyncio

        from miniagent.engine.btw_cmd import (
            cmd_btw_clear,
            cmd_btw_result,
            cmd_btw_start,
            cmd_btw_status,
        )

        # Clear first
        cmd_btw_clear()

        class MockEngine:
            async def run_agent_with_thinking(self, **kwargs):
                await asyncio.sleep(0.1)
                return "Integration test result"

        engine = MockEngine()
        state = {"skill_toolboxes": [], "skill_prompts": None}

        # Start task
        start_result = await cmd_btw_start(engine, "Integration test", state)
        assert "已启动" in start_result

        # Extract task ID from result
        # Format: "✅ 后台任务已启动: {task_id}"
        import re
        match = re.search(r"已启动: ([a-f0-9]+)", start_result)
        task_id = match.group(1) if match else None

        if task_id:
            # Wait for completion
            await asyncio.sleep(0.2)

            # Check status
            status_result = cmd_btw_status(task_id)
            assert task_id in status_result

            # Get result
            result = await cmd_btw_result(task_id)
            assert "结果" in result

            # Clear
            clear_result = cmd_btw_clear()
            assert "已清理" in clear_result or "没有需要清理" in clear_result