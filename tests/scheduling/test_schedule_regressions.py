"""Focused regressions migrated from test_final_diff_coverage_matrix.py."""

from __future__ import annotations

import importlib
from unittest.mock import MagicMock

import pytest

from miniagent.assistant.scheduled_tasks.models import ScheduledTask, ScheduleSpec

schedule_tools = importlib.import_module("miniagent.assistant.tools.schedule_tools")

def test_updated_schedule_all_kinds_and_errors() -> None:
    existing = ScheduledTask(
        id="task",
        name="task",
        prompt="p",
        schedule=ScheduleSpec(kind="interval", interval_seconds=60, timezone="UTC"),
    )
    assert schedule_tools._updated_schedule(
        existing, {"schedule_kind": "interval", "interval_seconds": 5}, "UTC"
    ).interval_seconds == 5
    assert schedule_tools._updated_schedule(
        existing,
        {"schedule_kind": "once", "once_iso": "2035-01-01T00:00:00Z"},
        "UTC",
    ).kind == "once"
    assert schedule_tools._updated_schedule(
        existing, {"schedule_kind": "cron", "cron_expr": "0 1 * * *"}, "UTC"
    ).kind == "cron"
    with pytest.raises(ValueError, match="正整数"):
        schedule_tools._updated_schedule(
            existing, {"schedule_kind": "interval", "interval_seconds": 0}, "UTC"
        )
    with pytest.raises(ValueError, match="once_iso"):
        schedule_tools._updated_schedule(
            existing, {"schedule_kind": "once", "once_iso": ""}, "UTC"
        )

def test_set_enabled_repairs_missing_next_run(monkeypatch: pytest.MonkeyPatch) -> None:
    task = ScheduledTask(id="task", name="task", prompt="p", enabled=False, next_run_at=None)
    monkeypatch.setattr("miniagent.assistant.scheduled_tasks.store.load_tasks", lambda: [task])
    monkeypatch.setattr(
        "miniagent.assistant.scheduled_tasks.store.compute_initial_next_run", lambda _task: 123.0
    )
    monkeypatch.setattr("miniagent.assistant.scheduled_tasks.store.repair_invalid_schedules", lambda _tasks: False)
    monkeypatch.setattr("miniagent.assistant.scheduled_tasks.store.save_tasks", MagicMock())
    result = schedule_tools._schedule_tool_set_enabled(
        {"task_id": "task", "enabled": True}
    )
    assert result.success and task.next_run_at == 123.0

@pytest.mark.asyncio
async def test_schedule_add_invalid_next_run_and_update_no_save(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "miniagent.assistant.scheduled_tasks.store.compute_initial_next_run", lambda _task: None
    )
    add = schedule_tools._schedule_tool_add(
        {"action": "add_cron", "task_id": "x", "prompt": "p", "cron_expr": "0 1 * * *"}
    )
    assert not add.success and "cron" in add.content

    existing = ScheduledTask(id="x", name="x", prompt="old")
    monkeypatch.setattr("miniagent.assistant.scheduled_tasks.store.load_tasks", lambda: [existing])
    save = MagicMock()
    monkeypatch.setattr("miniagent.assistant.scheduled_tasks.store.save_tasks", save)
    update = schedule_tools._schedule_tool_update(
        {"action": "update", "task_id": "x", "prompt": "new", "interval_seconds": 10}
    )
    assert not update.success and "无法计算" in update.content
    save.assert_not_called()
