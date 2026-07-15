"""进程时区 SSOT 与定时任务 effective/align 时区。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from miniagent.agent.timezone import (
    format_agent_timezone_context,
    process_timezone,
)
from miniagent.assistant.scheduled_tasks.cron import cron_next_run_epoch
from miniagent.assistant.scheduled_tasks.models import ScheduledTask, ScheduleSpec
from miniagent.assistant.scheduled_tasks.store import effective_task_timezone
from miniagent.assistant.scheduled_tasks.timezone_util import default_schedule_timezone
from tests.config_helpers import install_test_config


def test_process_timezone_priority(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    install_test_config(
        tmp_path,
        {
            "timezone": {"default": "Europe/London"},
            "scheduled_tasks": {"timezone": "America/New_York"},
        },
    )
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    assert process_timezone() == "Europe/London"


def test_process_timezone_falls_back_to_tz(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    install_test_config(tmp_path, {"timezone": {"default": ""}})
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    assert process_timezone() == "Asia/Shanghai"


def test_process_timezone_ignores_schedule_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    install_test_config(
        tmp_path,
        {
            "timezone": {"default": ""},
            "scheduled_tasks": {"timezone": "America/New_York"},
        },
    )
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    assert process_timezone() == "Asia/Shanghai"


def test_default_schedule_timezone_uses_schedule_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    install_test_config(
        tmp_path,
        {
            "timezone": {"default": ""},
            "scheduled_tasks": {"timezone": "America/New_York"},
        },
    )
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    assert default_schedule_timezone() == "America/New_York"


def test_effective_task_timezone_utc(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    install_test_config(tmp_path, {"timezone": {"default": ""}})
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    task = ScheduledTask(
        id="t",
        name="t",
        prompt="p",
        schedule=ScheduleSpec(
            kind="cron",
            cron_expr="0 20 * * *",
            timezone="UTC",
        ),
    )
    assert effective_task_timezone(task) == "UTC"


def test_cron_20h_shanghai_wall_clock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    install_test_config(tmp_path, {"timezone": {"default": ""}})
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    tz = ZoneInfo("Asia/Shanghai")
    # 2026-05-17 10:00 Shanghai
    after = datetime(2026, 5, 17, 10, 0, 0, tzinfo=tz).timestamp()
    nxt = cron_next_run_epoch("0 20 * * *", "Asia/Shanghai", after)
    local = datetime.fromtimestamp(nxt, tz=tz)
    assert local.hour == 20
    assert local.minute == 0


def test_format_agent_timezone_context_contains_tz(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    install_test_config(tmp_path, {"timezone": {"default": ""}})
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    ctx = format_agent_timezone_context()
    assert "Asia/Shanghai" in ctx
    assert "本地时间" in ctx
