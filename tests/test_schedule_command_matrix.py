"""Boundary matrix for the split schedule command parser."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from miniagent.engine.commands import schedule_commands as commands
from miniagent.scheduled_tasks.models import ScheduledTask, ScheduleSpec, SessionSpec


def test_schedule_helper_parsers(monkeypatch) -> None:
    assert commands._schedule_head_strip_tz_tokens(
        ["add", "x", "--tz", "Asia/Shanghai", "every"]
    ) == (["add", "x", "every"], "Asia/Shanghai")
    assert commands._schedule_head_strip_tz_tokens(["--tz", "", "x"])[1] == "UTC"
    monkeypatch.setattr(
        "miniagent.scheduled_tasks.timezone_util.default_schedule_timezone",
        lambda: "Default/TZ",
    )
    assert commands._resolve_schedule_tz(None) == "Default/TZ"
    existing = SimpleNamespace(schedule=SimpleNamespace(timezone="Existing/TZ"))
    assert commands._resolve_schedule_tz(None, existing=existing) == "Existing/TZ"
    assert commands._resolve_schedule_tz("UTC", existing=existing) == "UTC"

    assert commands._parse_cron_add_tokens(
        ["add", "x", "cron", "0", "8", "*", "*", "*", "primary"]
    ) == ("0 8 * * *", "primary")
    assert commands._parse_cron_add_tokens(
        ["add", "x", "cron", "0 8 * * *", "primary"]
    ) == ("0 8 * * *", "primary")
    for tokens in (["bad"], ["add", "x", "cron"], ["add", "x", "cron", "a", "b", "c"]):
        with pytest.raises(ValueError):
            commands._parse_cron_add_tokens(tokens)

    assert commands._parse_schedule_session_spec("primary").mode == "primary"
    assert commands._parse_schedule_session_spec("ephemeral").mode == "ephemeral"
    fixed = commands._parse_schedule_session_spec("fixed:feishu:oc_x")
    assert fixed.session_id == "feishu:oc_x" and fixed.feishu_chat_id == "oc_x"
    with pytest.raises(ValueError):
        commands._parse_schedule_session_spec("fixed:")
    with pytest.raises(ValueError):
        commands._parse_schedule_session_spec("bad")


def test_schedule_read_and_mutation_validation(monkeypatch) -> None:
    tasks: list[ScheduledTask] = []
    saved: list[list[ScheduledTask]] = []
    monkeypatch.setattr("miniagent.scheduled_tasks.store.load_tasks", lambda: list(tasks))
    monkeypatch.setattr(
        "miniagent.scheduled_tasks.store.save_tasks", lambda value: saved.append(list(value))
    )
    monkeypatch.setattr(
        "miniagent.scheduled_tasks.store.format_next_run_display", lambda *_args, **_kwargs: "soon"
    )
    monkeypatch.setattr(
        "miniagent.scheduled_tasks.store.repair_invalid_schedules", lambda _tasks: False
    )
    monkeypatch.setattr(
        "miniagent.scheduled_tasks.store.compute_initial_next_run", lambda _task: 9999999999.0
    )

    assert commands.cmd_schedule("not-command", allow_mutations=True).startswith("定时任务")
    assert commands.cmd_schedule("/schedule", allow_mutations=True).startswith("定时任务")
    assert "暂无" in commands.cmd_schedule("/schedule list", allow_mutations=True)
    assert "未找到" in commands.cmd_schedule("/schedule show x", allow_mutations=True)
    assert "不允许修改" in commands.cmd_schedule(
        "/schedule remove x", allow_mutations=False
    )
    assert "未找到" in commands.cmd_schedule("/schedule remove x", allow_mutations=True)
    assert "未找到" in commands.cmd_schedule("/schedule enable x", allow_mutations=True)
    assert "未找到" in commands.cmd_schedule("/schedule disable x", allow_mutations=True)
    assert "分隔符" in commands.cmd_schedule(
        "/schedule add x every 10 primary", allow_mutations=True
    )
    assert "参数不足" in commands.cmd_schedule(
        "/schedule add x -- prompt", allow_mutations=True
    )
    assert "间隔须为正整数" in commands.cmd_schedule(
        "/schedule add x every 0 primary -- prompt", allow_mutations=True
    )
    assert "须为 every" in commands.cmd_schedule(
        "/schedule add x weekly 1 primary -- prompt", allow_mutations=True
    )

    added = commands.cmd_schedule(
        "/schedule add x every 10 primary -- prompt", allow_mutations=True
    )
    assert "已添加" in added and saved[-1][0].id == "x"
    tasks[:] = saved[-1]
    assert "已存在" in commands.cmd_schedule(
        "/schedule add x every 10 primary -- prompt", allow_mutations=True
    )
    tasks[0].last_error = "line1\nline2"
    assert "err:" in commands.cmd_schedule("/schedule list", allow_mutations=True)
    assert '"id": "x"' in commands.cmd_schedule("/schedule show x", allow_mutations=True)
    assert "已禁用" in commands.cmd_schedule("/schedule disable x", allow_mutations=True)
    tasks[0].enabled = False
    assert "已启用" in commands.cmd_schedule("/schedule enable x", allow_mutations=True)
    assert "已删除" in commands.cmd_schedule("/schedule remove x", allow_mutations=True)


def test_schedule_add_once_cron_and_update_errors(monkeypatch) -> None:
    task = ScheduledTask(
        id="existing",
        name="existing",
        prompt="old",
        schedule=ScheduleSpec(kind="interval", interval_seconds=10, timezone="UTC"),
        session=SessionSpec(mode="primary"),
    )
    tasks = [task]
    monkeypatch.setattr("miniagent.scheduled_tasks.store.load_tasks", lambda: tasks)
    monkeypatch.setattr("miniagent.scheduled_tasks.store.save_tasks", lambda _value: None)
    monkeypatch.setattr(
        "miniagent.scheduled_tasks.store.repair_invalid_schedules", lambda _tasks: False
    )
    monkeypatch.setattr(
        "miniagent.scheduled_tasks.store.format_next_run_display", lambda *_args, **_kwargs: "soon"
    )
    next_runs = iter([None, 1.0, None, 9999999999.0, 9999999999.0, None])
    monkeypatch.setattr(
        "miniagent.scheduled_tasks.store.compute_initial_next_run", lambda _task: next(next_runs)
    )

    assert "无法解析 once" in commands.cmd_schedule(
        "/schedule add once1 once 2030-01-01T00:00:00Z primary -- p",
        allow_mutations=True,
    )
    assert "已在过去" in commands.cmd_schedule(
        "/schedule add once2 once 2000-01-01T00:00:00Z primary -- p",
        allow_mutations=True,
    )
    assert "无法根据 cron" in commands.cmd_schedule(
        '/schedule add cron1 cron "0 8 * * *" primary -- p',
        allow_mutations=True,
    )
    assert "已添加" in commands.cmd_schedule(
        '/schedule add cron2 cron "0 8 * * *" primary -- p',
        allow_mutations=True,
    )

    assert "分隔符" in commands.cmd_schedule(
        "/schedule update existing every 20 primary", allow_mutations=True
    )
    assert "未找到" in commands.cmd_schedule(
        "/schedule update missing every 20 primary -- p", allow_mutations=True
    )
    assert "间隔须为正整数" in commands.cmd_schedule(
        "/schedule update existing every 0 primary -- p", allow_mutations=True
    )
    assert "须为 every" in commands.cmd_schedule(
        "/schedule update existing weekly 1 primary -- p", allow_mutations=True
    )
    assert "已更新" in commands.cmd_schedule(
        "/schedule update existing every 20 primary -- p", allow_mutations=True
    )
    assert "无法计算" in commands.cmd_schedule(
        "/schedule update existing once 2030-01-01T00:00:00Z primary -- p",
        allow_mutations=True,
    )
