"""scheduled_tasks_loop / start_scheduled_tasks_ticker / shutdown 集成。"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from miniagent.engine.cli_state import CliLoopState
from miniagent.engine.feishu_state import FeishuRuntime
from miniagent.engine.shutdown import shutdown_runtime
from miniagent.infrastructure.message_queue import MessageQueueManager
from miniagent.runtime.context import RuntimeContext
from miniagent.scheduled_tasks.models import ScheduledTask, ScheduleSpec, SessionSpec
from miniagent.scheduled_tasks.store import save_tasks
from miniagent.scheduled_tasks.ticker import (
    scheduled_tasks_loop,
    start_scheduled_tasks_ticker,
    tick_once,
)
from tests.scheduled_tasks_helpers import minimal_cli_state, minimal_tick_ctx, patch_tick_once_locks


def _runtime_ctx() -> RuntimeContext:
    mq = MessageQueueManager()
    router = MagicMock()
    router.primary = "default"
    return RuntimeContext(
        registry=MagicMock(),
        monitor=MagicMock(),
        skill_registry=MagicMock(),
        clawhub=MagicMock(),
        engine=MagicMock(),
        channel_router=router,
        message_queue=mq,
        feishu=FeishuRuntime(mq),
        memory_store=MagicMock(),
        activity_log=MagicMock(),
        keyword_index=MagicMock(),
        openai_client=None,
    )


@pytest.mark.asyncio
async def test_scheduled_tasks_loop_invokes_tick_once(
    state_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []

    async def _fake_tick(*_a: object, **_k: object) -> None:
        calls.append(1)

    monkeypatch.setattr("miniagent.scheduled_tasks.ticker.tick_once", _fake_tick)
    monkeypatch.setattr(
        "miniagent.scheduled_tasks.ticker._sleep_seconds_until", lambda _tasks: 0.01
    )

    ctx = minimal_tick_ctx()
    st = minimal_cli_state(ctx)
    stop = asyncio.Event()
    loop_task = asyncio.create_task(scheduled_tasks_loop(ctx, st, [], [], stop))
    await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(loop_task, timeout=2.0)
    assert len(calls) >= 1


@pytest.mark.asyncio
async def test_start_scheduled_tasks_ticker_sets_ctx_fields(
    state_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = minimal_tick_ctx()
    st = minimal_cli_state(ctx)
    ctx.scheduled_tasks_stop_event = None
    ctx.scheduled_tasks_ticker = None

    monkeypatch.setattr(
        "miniagent.scheduled_tasks.ticker.scheduled_tasks_loop",
        AsyncMock(),
    )
    t = start_scheduled_tasks_ticker(ctx, st, [], [])
    assert ctx.scheduled_tasks_stop_event is not None
    assert ctx.scheduled_tasks_ticker is t
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t


@pytest.mark.asyncio
async def test_shutdown_runtime_cancels_scheduled_tasks_ticker() -> None:
    ctx = _runtime_ctx()
    st: CliLoopState = {
        "active_session_id": "",
        "skill_toolboxes": [],
        "skill_prompts": [],
        "feishu_enabled": False,
        "session_manager": None,
        "instance_id": 1,
        "runtime_ctx": ctx,
        "feishu_p2p_synced_senders": set(),
    }

    async def _slow_loop() -> None:
        await asyncio.sleep(3600)

    ticker_task = asyncio.create_task(_slow_loop(), name="miniagent_scheduled_tasks")
    ctx.scheduled_tasks_ticker = ticker_task
    ctx.scheduled_tasks_stop_event = asyncio.Event()

    await shutdown_runtime(
        ctx,
        st,
        reason="test_ticker_shutdown",
        abort_message_queues=False,
        release_cli_session_lock=False,
        call_unregister=False,
        shutdown_default_executor=False,
    )
    assert ticker_task.done()


@pytest.mark.asyncio
async def test_tick_once_respects_max_due_per_tick(
    state_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MINIAGENT_DISABLE_SCHEDULED_TASKS", "0")
    now = time.time() - 1.0
    tasks = [
        ScheduledTask(
            id=f"due{i}",
            name=f"due{i}",
            prompt="p",
            enabled=True,
            schedule=ScheduleSpec(kind="interval", interval_seconds=3600),
            session=SessionSpec(mode="primary"),
            next_run_at=now,
        )
        for i in range(6)
    ]
    save_tasks(tasks)

    dispatched: list[str] = []

    def _fake_build(_ctx, _state, task, *_a, **_k):
        async def _coro() -> None:
            dispatched.append(task.id)

        return (_coro(), "__cli__")

    monkeypatch.setattr(
        "miniagent.scheduled_tasks.ticker.build_run_scheduled_job_coro", _fake_build
    )
    patch_tick_once_locks(monkeypatch)

    ctx = minimal_tick_ctx()
    st = minimal_cli_state(ctx)
    await tick_once(ctx, st, [], [])
    await asyncio.sleep(0.2)
    assert len(dispatched) == 5
