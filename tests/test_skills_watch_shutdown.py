"""Skills watch task stops on shutdown_runtime."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from miniagent.engine.shutdown import shutdown_runtime
from miniagent.runtime.context import RuntimeContext


@pytest.mark.asyncio
async def test_shutdown_stops_skills_watch() -> None:
    ctx = RuntimeContext(
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
    )
    stop_event = asyncio.Event()
    ctx.skills_watch_stop_event = stop_event

    async def _hang() -> None:
        await stop_event.wait()

    ctx.skills_watch_task = asyncio.create_task(_hang(), name="test_skills_watch")

    state: dict = {"active_session_id": ""}
    ctx.message_queue.abort_all_chats = MagicMock()
    ctx.feishu.stop_async = AsyncMock(return_value=None)

    async def _noop_cleanup() -> None:
        pass

    async def _noop_dream() -> None:
        pass

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("miniagent.engine.shutdown.cleanup_all_processes", _noop_cleanup)
        mp.setattr(
            "miniagent.memory.dream_scheduler.cancel_pending_dream_tasks",
            _noop_dream,
        )
        await shutdown_runtime(
            ctx,
            state,  # type: ignore[arg-type]
            reason="test",
            abort_message_queues=False,
            release_cli_session_lock=False,
            call_unregister=False,
        )

    assert stop_event.is_set()
    assert ctx.skills_watch_task.done()
