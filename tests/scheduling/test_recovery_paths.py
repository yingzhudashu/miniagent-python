"""Focused regressions migrated from test_recovery_edge_matrix.py."""

from __future__ import annotations

import queue
from unittest.mock import MagicMock

import pytest

from miniagent.agent.observability import AsyncTraceWriter
from miniagent.assistant.scheduled_tasks import ticker
from miniagent.assistant.scheduled_tasks.models import ScheduledTask, ScheduleSpec


def test_scheduler_sleep_selection_and_trace_write_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert ticker._sleep_seconds_until([]) == 60.0
    monkeypatch.setattr(ticker.time, "time", lambda: 100.0)
    active = ScheduledTask(
        id="active",
        name="active",
        prompt="p",
        schedule=ScheduleSpec(kind="interval", interval_seconds=60),
        next_run_at=101.0,
    )
    disabled = ScheduledTask(id="disabled", name="d", prompt="p", enabled=False, next_run_at=1)
    future = ScheduledTask(id="future", name="f", prompt="p", next_run_at=200)
    assert ticker._sleep_seconds_until([active]) == 1.0
    monkeypatch.setattr(ticker, "try_acquire_job_lock", lambda _id: True)
    assert ticker._select_due_tasks([disabled, future, active], 150) == [active]

    writer = AsyncTraceWriter()
    writer._flush_trace_lines = MagicMock(side_effect=OSError("disk"))
    writer._write_trace_batch([("2026-01-01", "{}\n")])
    assert writer._write_error_count == 1 and writer._dropped_count == 1
    assert writer._collect_writer_batch() is None
    writer._queue = queue.Queue(maxsize=1)
    writer._queue.put_nowait({"type": "x"})
    writer._shutdown = True
    batch = writer._collect_writer_batch()
    assert batch is not None

@pytest.mark.asyncio
async def test_scheduler_finalize_failure_releases_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    ticker._inflight.add("job")
    release = MagicMock()
    monkeypatch.setattr(ticker, "load_tasks", MagicMock(side_effect=RuntimeError("disk")))
    monkeypatch.setattr(ticker, "release_job_lock", release)
    await ticker._finalize_scheduled_job("job", outcome="completed", agent_error=None)
    assert "job" not in ticker._inflight
    release.assert_called_once_with("job")
