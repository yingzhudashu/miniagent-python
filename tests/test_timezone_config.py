"""进程时区 SSOT 与定时任务 effective/align 时区。"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from miniagent.infrastructure.timezone_config import (
    format_agent_timezone_context,
    process_timezone,
)
from miniagent.scheduled_tasks.cron import cron_next_run_epoch
from miniagent.scheduled_tasks.models import ScheduledTask, ScheduleSpec, SessionSpec
from miniagent.scheduled_tasks.store import (
    align_task_timezones_to_env,
    effective_task_timezone,
)
from miniagent.scheduled_tasks.timezone_util import default_schedule_timezone


def test_process_timezone_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINIAGENT_TIMEZONE", "Europe/London")
    monkeypatch.setenv("MINIAGENT_SCHEDULED_TASKS_TIMEZONE", "America/New_York")
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    assert process_timezone() == "Europe/London"


def test_process_timezone_falls_back_to_tz(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MINIAGENT_TIMEZONE", raising=False)
    monkeypatch.delenv("MINIAGENT_SCHEDULED_TASKS_TIMEZONE", raising=False)
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    assert process_timezone() == "Asia/Shanghai"


def test_process_timezone_ignores_schedule_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MINIAGENT_TIMEZONE", raising=False)
    monkeypatch.setenv("MINIAGENT_SCHEDULED_TASKS_TIMEZONE", "America/New_York")
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    assert process_timezone() == "Asia/Shanghai"


def test_default_schedule_timezone_uses_schedule_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MINIAGENT_TIMEZONE", raising=False)
    monkeypatch.setenv("MINIAGENT_SCHEDULED_TASKS_TIMEZONE", "America/New_York")
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    assert default_schedule_timezone() == "America/New_York"


def test_effective_task_timezone_legacy_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MINIAGENT_TIMEZONE", raising=False)
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    task = ScheduledTask(
        id="t",
        name="t",
        prompt="p",
        schedule=ScheduleSpec(kind="cron", cron_expr="0 20 * * *", timezone="UTC"),
    )
    assert effective_task_timezone(task) == "Asia/Shanghai"


def test_effective_task_timezone_explicit_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    task = ScheduledTask(
        id="t",
        name="t",
        prompt="p",
        schedule=ScheduleSpec(
            kind="cron",
            cron_expr="0 20 * * *",
            timezone="UTC",
            timezone_explicit=True,
        ),
    )
    assert effective_task_timezone(task) == "UTC"


def test_cron_20h_shanghai_wall_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    tz = ZoneInfo("Asia/Shanghai")
    # 2026-05-17 10:00 Shanghai
    after = datetime(2026, 5, 17, 10, 0, 0, tzinfo=tz).timestamp()
    nxt = cron_next_run_epoch("0 20 * * *", "Asia/Shanghai", after)
    local = datetime.fromtimestamp(nxt, tz=tz)
    assert local.hour == 20
    assert local.minute == 0


def test_align_task_timezones_to_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    task = ScheduledTask(
        id="daily",
        name="daily",
        prompt="p",
        enabled=True,
        schedule=ScheduleSpec(kind="cron", cron_expr="0 20 * * *", timezone="UTC"),
        session=SessionSpec(mode="primary"),
    )
    tz = ZoneInfo("Asia/Shanghai")
    task.next_run_at = datetime(2026, 5, 17, 20, 0, 0, tzinfo=ZoneInfo("UTC")).timestamp()
    n, lines = align_task_timezones_to_env([task])
    assert n == 1
    assert task.schedule.timezone == "Asia/Shanghai"
    assert task.next_run_at is not None
    local = datetime.fromtimestamp(task.next_run_at, tz=tz)
    assert local.hour == 20


def test_format_agent_timezone_context_contains_tz(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    ctx = format_agent_timezone_context()
    assert "Asia/Shanghai" in ctx
    assert "本地时间" in ctx
