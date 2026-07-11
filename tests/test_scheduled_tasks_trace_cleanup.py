"""trace_cleanup 节流与 ticker 集成。"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from miniagent.scheduled_tasks import trace_cleanup as tc_mod
from miniagent.scheduled_tasks.ticker import scheduled_tasks_loop
from tests.config_helpers import install_test_config
from tests.scheduled_tasks_helpers import minimal_cli_state, minimal_tick_ctx


def _reset_cleanup_clock() -> None:
    tc_mod._last_cleanup_at = 0.0


def test_maybe_scheduled_cleanup_traces_runs_when_due(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset_cleanup_clock()
    calls: list[int] = []

    def _fake_cleanup() -> dict[str, object]:
        calls.append(1)
        return {"success": True, "deleted_count": 2}

    monkeypatch.setattr(tc_mod, "scheduled_cleanup_traces", _fake_cleanup)
    result = tc_mod.maybe_scheduled_cleanup_traces()
    assert result == {"success": True, "deleted_count": 2}
    assert len(calls) == 1


def test_maybe_scheduled_cleanup_traces_throttles(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset_cleanup_clock()
    calls: list[int] = []

    def _fake_cleanup() -> dict[str, object]:
        calls.append(1)
        return {"success": True, "deleted_count": 0}

    monkeypatch.setattr(tc_mod, "scheduled_cleanup_traces", _fake_cleanup)
    assert tc_mod.maybe_scheduled_cleanup_traces() is not None
    assert tc_mod.maybe_scheduled_cleanup_traces() is None
    assert len(calls) == 1


def test_maybe_scheduled_cleanup_traces_disabled(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _reset_cleanup_clock()
    install_test_config(tmp_path, {"trace": {"auto_cleanup": False}})
    calls: list[int] = []
    monkeypatch.setattr(
        tc_mod,
        "scheduled_cleanup_traces",
        lambda: calls.append(1) or {"success": True},
    )
    assert tc_mod.maybe_scheduled_cleanup_traces() is None
    assert calls == []


@pytest.mark.asyncio
async def test_scheduled_tasks_loop_invokes_trace_housekeeping(
    state_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    cleanup_calls: list[int] = []
    stats_calls: list[int] = []

    monkeypatch.setattr(
        "miniagent.scheduled_tasks.trace_cleanup.maybe_scheduled_cleanup_traces",
        lambda: cleanup_calls.append(1),
    )
    monkeypatch.setattr(
        "miniagent.scheduled_tasks.trace_cleanup.maybe_scheduled_trace_stats_report",
        lambda: stats_calls.append(1),
    )
    monkeypatch.setattr(
        "miniagent.scheduled_tasks.ticker.tick_once",
        AsyncMock(),
    )
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
    assert len(cleanup_calls) >= 1
    assert len(stats_calls) >= 1
