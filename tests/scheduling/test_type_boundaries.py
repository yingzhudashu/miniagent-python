"""Focused regressions migrated from test_type_boundary_regressions.py."""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from miniagent.assistant.engine.commands.session_management import cmd_schedule
from miniagent.assistant.scheduled_tasks.models import ScheduledTask, ScheduleSpec
from miniagent.assistant.tools.schedule_tools import _manage_scheduled_task_handler


@pytest.mark.asyncio
async def test_schedule_tool_lists_cron_expression(monkeypatch: pytest.MonkeyPatch) -> None:
    task = ScheduledTask(
        id="cron-task",
        name="Cron",
        prompt="run",
        schedule=ScheduleSpec(kind="cron", cron_expr="0 8 * * *"),
    )
    import miniagent.assistant.scheduled_tasks.store as store

    monkeypatch.setattr(store, "load_tasks", lambda: [task])
    result = await _manage_scheduled_task_handler(
        {"action": "list"},
        SimpleNamespace(cli_dispatch_allow_mutations=True),
    )
    assert result.success
    assert 'cron "0 8 * * *"' in result.content

def test_cli_schedule_list_and_update_kinds(monkeypatch: pytest.MonkeyPatch) -> None:
    import miniagent.assistant.scheduled_tasks.store as store

    task = ScheduledTask(
        id="job",
        name="Job",
        prompt="old",
        schedule=ScheduleSpec(kind="cron", cron_expr="0 8 * * *"),
    )
    monkeypatch.setattr(store, "load_tasks", lambda: [task])
    monkeypatch.setattr(store, "save_tasks", lambda _tasks: None)
    monkeypatch.setattr(store, "compute_initial_next_run", lambda *_args: 1.0)
    assert 'cron "0 8 * * *"' in cmd_schedule("/schedule list", allow_mutations=True)
    once = cmd_schedule(
        "/schedule update job once 2030-01-01T00:00:00 primary -- new once",
        allow_mutations=True,
    )
    assert "已更新" in once
    cron = cmd_schedule(
        '/schedule update job cron "0 9 * * *" primary -- new cron',
        allow_mutations=True,
    )
    assert "已更新" in cron

def test_scheduled_task_save_preserves_primary_error_when_cleanup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import miniagent.assistant.scheduled_tasks.store as store

    monkeypatch.setattr(store, "tasks_file_path", lambda: str(tmp_path / "tasks.json"))
    monkeypatch.setattr(store, "tasks_json_lock", lambda: nullcontext())
    monkeypatch.setattr(
        store,
        "atomic_dump_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("replace")),
    )
    with pytest.raises(OSError, match="replace"):
        store.save_tasks([])
