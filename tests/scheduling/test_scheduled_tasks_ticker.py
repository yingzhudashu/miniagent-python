"""Scheduler loop, lifecycle-owned starter and dispatch integration tests."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from miniagent.assistant.scheduled_tasks.models import ScheduledTask, ScheduleSpec, SessionSpec
from miniagent.assistant.scheduled_tasks.runner import ScheduledJob
from miniagent.assistant.scheduled_tasks.store import save_tasks
from miniagent.assistant.scheduled_tasks.ticker import (
    scheduled_tasks_loop,
    start_scheduled_tasks_ticker,
    tick_once,
)
from miniagent.ui.messages import InboundMessage
from tests.support.scheduling import minimal_cli_state, minimal_tick_ctx, patch_tick_once_locks


@pytest.mark.asyncio
async def test_scheduled_tasks_loop_invokes_tick_once(
    state_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []

    async def _fake_tick(*_a: object, **_k: object) -> None:
        calls.append(1)

    monkeypatch.setattr("miniagent.assistant.scheduled_tasks.ticker.tick_once", _fake_tick)
    monkeypatch.setattr(
        "miniagent.assistant.scheduled_tasks.ticker._sleep_seconds_until", lambda _tasks: 0.01
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
async def test_start_scheduled_tasks_ticker_uses_lifecycle_stop_event(
    state_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = minimal_tick_ctx()
    st = minimal_cli_state(ctx)
    stop_event = asyncio.Event()

    monkeypatch.setattr(
        "miniagent.assistant.scheduled_tasks.ticker.scheduled_tasks_loop",
        AsyncMock(),
    )
    t = start_scheduled_tasks_ticker(ctx, st, [], [], stop_event)
    assert t.get_name() == "miniagent_scheduled_tasks"
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t


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
        async def _run(_message: InboundMessage) -> None:
            dispatched.append(task.id)

        return ScheduledJob(
            message=InboundMessage.create(
                channel="scheduler",
                conversation_id="__cli__",
                sender_id="scheduler",
                content=task.prompt,
                session_key="default",
                metadata={"queue_key": "__cli__", "task_id": task.id},
            ),
            queue_key="__cli__",
            run=_run,
        )

    monkeypatch.setattr(
        "miniagent.assistant.scheduled_tasks.ticker.build_scheduled_job", _fake_build
    )
    patch_tick_once_locks(monkeypatch)

    ctx = minimal_tick_ctx()
    st = minimal_cli_state(ctx)
    await tick_once(ctx, st, [], [])
    await asyncio.sleep(0.2)
    assert len(dispatched) == 5
