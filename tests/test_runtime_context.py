"""miniagent/runtime/context.py 的单元测试。

RuntimeContext 是组合根，进程级依赖集中管理。
本测试验证构造、repr 安全、任务登记、进程级登记辅助函数等功能。
"""

from __future__ import annotations

import asyncio

import pytest

from miniagent.runtime.context import (
    RuntimeContext,
    get_runtime_context,
    reset_runtime_context_for_tests,
    set_runtime_context,
)


class TestRuntimeContext:
    """RuntimeContext 基础测试。"""

    def setup_method(self) -> None:
        reset_runtime_context_for_tests()

    def teardown_method(self) -> None:
        reset_runtime_context_for_tests()

    def _make_ctx(self) -> RuntimeContext:
        """构造最小可用的 RuntimeContext（所有必填字段为 MagicMock）。"""
        from unittest.mock import MagicMock

        return RuntimeContext(
            registry=MagicMock(),
            monitor=MagicMock(),
            skill_registry=MagicMock(),
            clawhub=MagicMock(),
            engine=MagicMock(),
            channel_router=MagicMock(),
            message_queue=MagicMock(),
            feishu=MagicMock(),
            memory_store=MagicMock(),
            activity_log=MagicMock(),
            keyword_index=MagicMock(),
            memory_context=MagicMock(),
        )

    def test_construct_minimal(self) -> None:
        ctx = self._make_ctx()
        assert ctx.registry is not None
        assert ctx.openai_client is None  # default

    def test_repr_safe(self) -> None:
        """含 repr=False 的字段不应出现在 repr 中。"""
        ctx = self._make_ctx()
        r = repr(ctx)
        # 这些字段标记了 repr=False
        assert "create_feishu_handler_factory" not in r
        assert "cli_transcript_append" not in r
        assert "cli_transcript_coordinator" not in r
        assert "scheduled_tasks_ticker" not in r
        assert "skills_watch_task" not in r
        assert "shutdown_tracked_tasks" not in r

    @pytest.mark.asyncio
    async def test_register_shutdown_tracked_task(self) -> None:
        ctx = self._make_ctx()
        assert len(ctx.shutdown_tracked_tasks) == 0

        task = asyncio.create_task(asyncio.sleep(0))
        ctx.register_shutdown_tracked_task(task)
        assert len(ctx.shutdown_tracked_tasks) == 1

        # 等待任务完成，回调应自动移除
        await task
        await asyncio.sleep(0.01)  # 等待 done 回调
        assert len(ctx.shutdown_tracked_tasks) == 0

    @pytest.mark.asyncio
    async def test_register_shutdown_tracked_task_skips_done_task(self) -> None:
        ctx = self._make_ctx()
        task = asyncio.create_task(asyncio.sleep(0))
        await task

        ctx.register_shutdown_tracked_task(task)
        assert len(ctx.shutdown_tracked_tasks) == 0

    @pytest.mark.asyncio
    async def test_register_shutdown_tracked_task_idempotent(self) -> None:
        ctx = self._make_ctx()
        task = asyncio.create_task(asyncio.sleep(10))

        ctx.register_shutdown_tracked_task(task)
        ctx.register_shutdown_tracked_task(task)
        assert len(ctx.shutdown_tracked_tasks) == 1

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.01)
        assert len(ctx.shutdown_tracked_tasks) == 0


class TestRuntimeContextSingleton:
    """进程级 get/set/reset 辅助函数。"""

    def setup_method(self) -> None:
        reset_runtime_context_for_tests()

    def teardown_method(self) -> None:
        reset_runtime_context_for_tests()

    def test_get_before_set_returns_none(self) -> None:
        assert get_runtime_context() is None

    def test_set_and_get_roundtrip(self) -> None:
        from unittest.mock import MagicMock

        ctx = RuntimeContext(
            registry=MagicMock(),
            monitor=MagicMock(),
            skill_registry=MagicMock(),
            clawhub=None,
            engine=MagicMock(),
            channel_router=MagicMock(),
            message_queue=MagicMock(),
            feishu=None,
            memory_store=MagicMock(),
            activity_log=MagicMock(),
            keyword_index=MagicMock(),
            memory_context=MagicMock(),
        )
        set_runtime_context(ctx)
        assert get_runtime_context() is ctx

    def test_set_overwrites_previous(self) -> None:
        from unittest.mock import MagicMock

        first = RuntimeContext(
            registry=MagicMock(),
            monitor=MagicMock(),
            skill_registry=MagicMock(),
            clawhub=None,
            engine=MagicMock(),
            channel_router=MagicMock(),
            message_queue=MagicMock(),
            feishu=None,
            memory_store=MagicMock(),
            activity_log=MagicMock(),
            keyword_index=MagicMock(),
            memory_context=MagicMock(),
        )
        second = RuntimeContext(
            registry=MagicMock(),
            monitor=MagicMock(),
            skill_registry=MagicMock(),
            clawhub=None,
            engine=MagicMock(),
            channel_router=MagicMock(),
            message_queue=MagicMock(),
            feishu=None,
            memory_store=MagicMock(),
            activity_log=MagicMock(),
            keyword_index=MagicMock(),
            memory_context=MagicMock(),
        )
        set_runtime_context(first)
        set_runtime_context(second)
        assert get_runtime_context() is second

    def test_reset_clears_singleton(self) -> None:
        from unittest.mock import MagicMock

        ctx = RuntimeContext(
            registry=MagicMock(),
            monitor=MagicMock(),
            skill_registry=MagicMock(),
            clawhub=None,
            engine=MagicMock(),
            channel_router=MagicMock(),
            message_queue=MagicMock(),
            feishu=None,
            memory_store=MagicMock(),
            activity_log=MagicMock(),
            keyword_index=MagicMock(),
            memory_context=MagicMock(),
        )
        set_runtime_context(ctx)
        reset_runtime_context_for_tests()
        assert get_runtime_context() is None
