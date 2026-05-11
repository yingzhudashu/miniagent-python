from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from miniagent.engine.cli_state import CliLoopState
from miniagent.infrastructure.message_queue import MessageQueueManager
from miniagent.scheduled_tasks.models import ScheduledTask, ScheduleSpec, SessionSpec
from miniagent.scheduled_tasks.resolve import resolve_execution_target, should_run_feishu
from miniagent.scheduled_tasks.store import (
    compute_initial_next_run,
    load_tasks,
    recompute_next_after_run,
    save_tasks,
)
from miniagent.scheduled_tasks.ticker import tick_once


@pytest.fixture()
def state_dir(monkeypatch: pytest.MonkeyPatch) -> str:
    d = tempfile.mkdtemp()
    monkeypatch.setenv("MINI_AGENT_STATE", d)
    return d


def test_load_save_roundtrip(state_dir: str) -> None:
    t = ScheduledTask(
        id="j1",
        name="Test",
        prompt="hello",
        schedule=ScheduleSpec(kind="interval", interval_seconds=120),
        session=SessionSpec(mode="primary"),
        next_run_at=123.0,
    )
    save_tasks([t])
    loaded = load_tasks()
    assert len(loaded) == 1
    assert loaded[0].id == "j1"
    assert loaded[0].schedule.interval_seconds == 120
    assert loaded[0].next_run_at == 123.0


def test_compute_initial_interval(state_dir: str) -> None:
    t = ScheduledTask(
        id="a",
        name="a",
        prompt="p",
        schedule=ScheduleSpec(kind="interval", interval_seconds=60),
    )
    now = 1000.0
    n = compute_initial_next_run(t, now)
    assert n == 1060.0


def test_recompute_after_run_interval(state_dir: str) -> None:
    t = ScheduledTask(
        id="a",
        name="a",
        prompt="p",
        schedule=ScheduleSpec(kind="interval", interval_seconds=30),
        next_run_at=100.0,
    )
    recompute_next_after_run(t, now_ts=1000.0)
    assert t.next_run_at == 1030.0


def test_recompute_once_disables(state_dir: str) -> None:
    t = ScheduledTask(
        id="o",
        name="o",
        prompt="p",
        schedule=ScheduleSpec(kind="once", once_at_iso="2099-01-01T00:00:00+00:00"),
        enabled=True,
    )
    recompute_next_after_run(t, now_ts=1.0)
    assert t.enabled is False
    assert t.next_run_at is None


def test_cmd_schedule_remote_blocks_mutations() -> None:
    from miniagent.engine.cli_commands import cmd_schedule

    out = cmd_schedule(
        ".schedule add x every 60 primary -- hello",
        allow_mutations=False,
    )
    assert "不允许修改" in out or "渠道" in out


def test_cmd_schedule_add_parses(state_dir: str) -> None:
    from miniagent.engine.cli_commands import cmd_schedule

    line = ".schedule add t99 every 5 primary -- integration test prompt"
    msg = cmd_schedule(line, allow_mutations=True)
    assert "已添加" in msg
    tasks = load_tasks()
    assert any(x.id == "t99" for x in tasks)
    save_tasks([x for x in tasks if x.id != "t99"])


def test_cmd_schedule_add_once_with_tz_naive(state_dir: str) -> None:
    from miniagent.engine.cli_commands import cmd_schedule

    line = (
        ".schedule add tz1 once 2030-06-15T08:00:00 primary --tz Asia/Shanghai "
        "-- remind me"
    )
    msg = cmd_schedule(line, allow_mutations=True)
    assert "已添加" in msg
    tasks = load_tasks()
    t = next(x for x in tasks if x.id == "tz1")
    assert t.schedule.timezone == "Asia/Shanghai"
    assert t.next_run_at is not None
    save_tasks([x for x in tasks if x.id != "tz1"])


def test_resolve_primary_and_fixed_feishu(state_dir: str) -> None:
    router = MagicMock()
    router.primary = "sess-a"
    st: CliLoopState = {
        "active_session_id": "fallback",
        "skill_toolboxes": [],
        "skill_prompts": [],
        "feishu_enabled": True,
        "session_manager": None,
        "instance_id": 1,
        "runtime_ctx": None,
        "feishu_p2p_synced_senders": set(),
    }
    t1 = ScheduledTask(
        id="1",
        name="n",
        prompt="p",
        session=SessionSpec(mode="primary"),
    )
    sk, recv, mq = resolve_execution_target(t1, channel_router=router, state=st)
    assert sk == "sess-a"
    assert mq == "__cli__"

    t2 = ScheduledTask(
        id="2",
        name="n",
        prompt="p",
        session=SessionSpec(
            mode="fixed",
            session_id="feishu:oc_abc",
            feishu_chat_id="oc_abc",
        ),
    )
    sk2, recv2, mq2 = resolve_execution_target(t2, channel_router=router, state=st)
    assert sk2 == "feishu:oc_abc"
    assert recv2 == "oc_abc"
    assert mq2 == "oc_abc"
    assert should_run_feishu(sk2, recv2, feishu_enabled=True) is True


@pytest.mark.asyncio
async def test_tick_once_dispatches_and_updates(state_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINIAGENT_DISABLE_SCHEDULED_TASKS", "0")
    t = ScheduledTask(
        id="run1",
        name="run1",
        prompt="ping",
        enabled=True,
        schedule=ScheduleSpec(kind="interval", interval_seconds=3600),
        session=SessionSpec(mode="primary"),
        next_run_at=time.time() - 1.0,
    )
    save_tasks([t])

    router = MagicMock()
    router.primary = "default"

    mq = MessageQueueManager()
    engine = MagicMock()
    engine.run_agent_with_thinking = AsyncMock(return_value="ok")
    feishu_rt = MagicMock()
    feishu_rt.get_config.return_value = None

    ctx = SimpleNamespace(
        message_queue=mq,
        channel_router=router,
        engine=engine,
        registry=None,
        monitor=None,
        clawhub=None,
        memory_store=None,
        activity_log=None,
        keyword_index=None,
        openai_client=None,
        cli_transcript_append=None,
        feishu=feishu_rt,
    )

    st: CliLoopState = {
        "active_session_id": "default",
        "skill_toolboxes": [],
        "skill_prompts": [],
        "feishu_enabled": False,
        "session_manager": None,
        "instance_id": 1,
        "runtime_ctx": ctx,
        "feishu_p2p_synced_senders": set(),
    }

    monkeypatch.setattr(
        "miniagent.scheduled_tasks.ticker.try_acquire_scheduler_lock", lambda: True
    )
    monkeypatch.setattr(
        "miniagent.scheduled_tasks.ticker.release_scheduler_lock", lambda: None
    )

    await tick_once(ctx, st, [], [])
    await asyncio.sleep(0.25)

    assert engine.run_agent_with_thinking.await_count == 1

    path = os.path.join(state_dir, "scheduled_tasks", "tasks.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    row = data["tasks"][0]
    assert row["run_count"] >= 1
    assert row["last_run_at"] is not None
    assert row.get("next_run_at") is not None
