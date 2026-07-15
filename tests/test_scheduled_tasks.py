from __future__ import annotations

import asyncio
import json
import os
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from miniagent.assistant.contracts.messages import InboundMessage
from miniagent.assistant.engine.cli_state import CliLoopState
from miniagent.assistant.scheduled_tasks.models import ScheduledTask, ScheduleSpec, SessionSpec
from miniagent.assistant.scheduled_tasks.resolve import resolve_execution_target, should_run_feishu
from miniagent.assistant.scheduled_tasks.runner import ScheduledJob
from miniagent.assistant.scheduled_tasks.store import (
    apply_dispatch_failure_backoff,
    compute_initial_next_run,
    dispatch_failure_backoff_seconds,
    finalize_task_after_run,
    load_tasks,
    recompute_next_after_run,
    repair_invalid_schedules,
    save_tasks,
    save_tasks_async,
)
from miniagent.assistant.scheduled_tasks.ticker import tick_once
from tests.config_helpers import install_test_config
from tests.scheduled_tasks_helpers import (
    minimal_cli_state,
    minimal_tick_ctx,
    patch_tick_once_locks,
)


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
    from miniagent.assistant.engine.cli_commands import cmd_schedule

    out = cmd_schedule(
        "/schedule add x every 60 primary -- hello",
        allow_mutations=False,
    )
    assert "不允许修改定时任务" in out


def test_cmd_schedule_once_insufficient_args(state_dir: str) -> None:
    from miniagent.assistant.engine.cli_commands import cmd_schedule

    out = cmd_schedule(
        "/schedule add bad once 2030-01-01 -- oops",
        allow_mutations=True,
    )
    assert "参数不足" in out or "用法" in out


def test_cmd_schedule_add_parses(state_dir: str) -> None:
    from miniagent.assistant.engine.cli_commands import cmd_schedule

    line = "/schedule add t99 every 5 primary -- integration test prompt"
    msg = cmd_schedule(line, allow_mutations=True)
    assert "已添加" in msg
    tasks = load_tasks()
    assert any(x.id == "t99" for x in tasks)
    save_tasks([x for x in tasks if x.id != "t99"])


def test_cmd_schedule_add_once_with_tz_naive(state_dir: str) -> None:
    from miniagent.assistant.engine.cli_commands import cmd_schedule

    line = "/schedule add tz1 once 2030-06-15T08:00:00 primary --tz Asia/Shanghai -- remind me"
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
async def test_tick_once_dispatches_and_updates(
    state_dir: str, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_test_config(
        tmp_path,
        {"paths": {"state_dir": state_dir}, "scheduled_tasks": {"disabled": False}},
    )
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

    engine = MagicMock()
    engine.run_agent_with_thinking = AsyncMock(return_value="ok")
    ctx = minimal_tick_ctx(engine=engine)
    st = minimal_cli_state(ctx)
    patch_tick_once_locks(monkeypatch)

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


def test_apply_dispatch_failure_backoff(state_dir: str) -> None:
    t = ScheduledTask(
        id="b",
        name="b",
        prompt="p",
        schedule=ScheduleSpec(kind="interval", interval_seconds=60),
        next_run_at=100.0,
    )
    apply_dispatch_failure_backoff(t, now_ts=1000.0)
    assert t.next_run_at == 1000.0 + dispatch_failure_backoff_seconds()


@pytest.mark.asyncio
async def test_tick_once_dispatch_failure_backoff(
    state_dir: str, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_test_config(
        tmp_path,
        {"paths": {"state_dir": state_dir}, "scheduled_tasks": {"disabled": False}},
    )
    t = ScheduledTask(
        id="fail1",
        name="fail1",
        prompt="p",
        enabled=True,
        schedule=ScheduleSpec(kind="interval", interval_seconds=3600),
        session=SessionSpec(mode="primary"),
        next_run_at=time.time() - 1.0,
    )
    save_tasks([t])

    ctx = minimal_tick_ctx()
    st = minimal_cli_state(ctx)

    def _boom(*_a: object, **_k: object) -> ScheduledJob:
        async def _run(_message: InboundMessage) -> None:
            raise RuntimeError("dispatch boom")

        return ScheduledJob(
            message=InboundMessage.create(
                channel="scheduler",
                conversation_id="__cli__",
                sender_id="scheduler",
                content="p",
                session_key="default",
                metadata={"queue_key": "__cli__", "task_id": "fail1"},
            ),
            queue_key="__cli__",
            run=_run,
        )

    monkeypatch.setattr("miniagent.assistant.scheduled_tasks.ticker.build_scheduled_job", _boom)
    patch_tick_once_locks(monkeypatch)

    before = time.time()
    await tick_once(ctx, st, [], [])
    await asyncio.sleep(0.25)

    loaded = load_tasks()[0]
    assert loaded.next_run_at is not None
    assert loaded.next_run_at >= before + dispatch_failure_backoff_seconds() - 2


def test_finalize_skipped_does_not_change_next(state_dir: str) -> None:
    t = ScheduledTask(
        id="sk",
        name="sk",
        prompt="p",
        schedule=ScheduleSpec(kind="interval", interval_seconds=60),
        next_run_at=50.0,
        run_count=3,
    )
    finalize_task_after_run(t, outcome="skipped")
    assert t.next_run_at == 50.0
    assert t.run_count == 3


def test_finalize_cancelled_preserves_next(state_dir: str) -> None:
    t = ScheduledTask(
        id="cn",
        name="cn",
        prompt="p",
        schedule=ScheduleSpec(kind="interval", interval_seconds=60),
        next_run_at=50.0,
    )
    finalize_task_after_run(t, outcome="cancelled")
    assert t.next_run_at == 50.0


def test_repair_invalid_cron_sets_error(state_dir: str) -> None:
    t = ScheduledTask(
        id="bad",
        name="bad",
        prompt="p",
        enabled=True,
        schedule=ScheduleSpec(kind="cron", cron_expr="not a cron", timezone="UTC"),
        next_run_at=1.0,
    )
    changed = repair_invalid_schedules([t])
    assert changed
    assert t.next_run_at is None
    assert "invalid cron" in (t.last_error or "")


def test_cmd_schedule_add_cron(state_dir: str) -> None:
    from miniagent.assistant.engine.cli_commands import cmd_schedule

    line = '/schedule add cron1 cron "10 8 * * *" primary --tz Asia/Shanghai -- daily job'
    msg = cmd_schedule(line, allow_mutations=True)
    assert "已添加" in msg
    tasks = load_tasks()
    t = next(x for x in tasks if x.id == "cron1")
    assert t.schedule.kind == "cron"
    assert t.schedule.cron_expr == "10 8 * * *"
    assert t.schedule.timezone == "Asia/Shanghai"
    assert t.next_run_at is not None
    save_tasks([x for x in tasks if x.id != "cron1"])


def test_compute_initial_cron(state_dir: str) -> None:
    t = ScheduledTask(
        id="c",
        name="c",
        prompt="p",
        schedule=ScheduleSpec(kind="cron", cron_expr="0 12 * * *", timezone="UTC"),
    )
    n = compute_initial_next_run(t, now_ts=1_700_000_000.0)
    assert n is not None
    assert n > 1_700_000_000.0


def test_recompute_after_run_cron(state_dir: str) -> None:
    t = ScheduledTask(
        id="c",
        name="c",
        prompt="p",
        schedule=ScheduleSpec(kind="cron", cron_expr="0 12 * * *", timezone="UTC"),
        last_run_at=1_700_000_000.0,
    )
    recompute_next_after_run(t, now_ts=1_700_000_000.0)
    assert t.next_run_at is not None
    assert t.next_run_at > 1_700_000_000.0


def test_finalize_completed_increments_run_count(state_dir: str) -> None:
    t = ScheduledTask(
        id="done",
        name="done",
        prompt="p",
        schedule=ScheduleSpec(kind="interval", interval_seconds=60),
        next_run_at=50.0,
        run_count=2,
    )
    finalize_task_after_run(t, outcome="completed", now_ts=100.0)
    assert t.run_count == 3
    assert t.last_run_at == 100.0
    assert t.next_run_at == 160.0


def test_finalize_dispatch_failed_backoff(state_dir: str) -> None:
    t = ScheduledTask(
        id="df",
        name="df",
        prompt="p",
        schedule=ScheduleSpec(kind="interval", interval_seconds=60),
        next_run_at=50.0,
    )
    finalize_task_after_run(t, outcome="dispatch_failed", now_ts=1000.0)
    assert t.next_run_at == 1000.0 + dispatch_failure_backoff_seconds()
    assert "dispatch" in (t.last_error or "")


def test_format_next_run_display_shows_relative(state_dir: str) -> None:
    from miniagent.assistant.scheduled_tasks.store import format_next_run_display

    t = ScheduledTask(
        id="fmt",
        name="fmt",
        prompt="p",
        schedule=ScheduleSpec(kind="interval", interval_seconds=60),
        next_run_at=time.time() + 30,
    )
    s = format_next_run_display(t)
    assert "in" in s or "due" in s


def test_compute_initial_interval_zero_returns_none(state_dir: str) -> None:
    t = ScheduledTask(
        id="z",
        name="z",
        prompt="p",
        schedule=ScheduleSpec(kind="interval", interval_seconds=0),
    )
    assert compute_initial_next_run(t, now_ts=1.0) is None


def test_resolve_ephemeral_session(state_dir: str) -> None:
    router = MagicMock()
    st: CliLoopState = {
        "active_session_id": "default",
        "skill_toolboxes": [],
        "skill_prompts": [],
        "feishu_enabled": False,
        "session_manager": None,
        "instance_id": 1,
        "runtime_ctx": None,
        "feishu_p2p_synced_senders": set(),
    }
    t = ScheduledTask(
        id="eph1",
        name="eph",
        prompt="p",
        session=SessionSpec(mode="ephemeral"),
    )
    sk, recv, mq = resolve_execution_target(t, channel_router=router, state=st)
    assert sk.startswith("sched_eph1_")
    assert recv is None
    assert mq == "__cli__"


def test_resolve_primary_falls_back_to_active_session(state_dir: str) -> None:
    router = MagicMock()
    router.primary = None
    st: CliLoopState = {
        "active_session_id": "active-fallback",
        "skill_toolboxes": [],
        "skill_prompts": [],
        "feishu_enabled": False,
        "session_manager": None,
        "instance_id": 1,
        "runtime_ctx": None,
        "feishu_p2p_synced_senders": set(),
    }
    t = ScheduledTask(
        id="p1",
        name="p",
        prompt="p",
        session=SessionSpec(mode="primary"),
    )
    sk, _, mq = resolve_execution_target(t, channel_router=router, state=st)
    assert sk == "active-fallback"
    assert mq == "__cli__"


def test_resolve_feishu_p2p_without_chat_id(state_dir: str) -> None:
    router = MagicMock()
    st: CliLoopState = {
        "active_session_id": "default",
        "skill_toolboxes": [],
        "skill_prompts": [],
        "feishu_enabled": True,
        "session_manager": None,
        "instance_id": 1,
        "runtime_ctx": None,
        "feishu_p2p_synced_senders": set(),
    }
    t = ScheduledTask(
        id="p2p",
        name="p2p",
        prompt="p",
        session=SessionSpec(mode="fixed", session_id="feishu_p2p:ou_abc"),
    )
    sk, recv, mq = resolve_execution_target(t, channel_router=router, state=st)
    assert sk == "feishu_p2p:ou_abc"
    # 未设 feishu_chat_id 时 recv 为 ""（非 None），且走 CLI 队列
    assert not (recv or "").strip()
    assert mq == "__cli__"
    assert should_run_feishu(sk, recv, feishu_enabled=True) is False


def test_cmd_schedule_list_and_show(state_dir: str) -> None:
    from miniagent.assistant.engine.cli_commands import cmd_schedule

    save_tasks(
        [
            ScheduledTask(
                id="ls1",
                name="ls1",
                prompt="p",
                enabled=True,
                schedule=ScheduleSpec(kind="interval", interval_seconds=120),
                session=SessionSpec(mode="primary"),
                next_run_at=time.time() + 3600,
            )
        ]
    )
    listed = cmd_schedule("/schedule list", allow_mutations=True)
    assert "ls1" in listed
    assert "定时任务" in listed
    shown = cmd_schedule("/schedule show ls1", allow_mutations=True)
    assert '"id": "ls1"' in shown


def test_cmd_schedule_remove_enable_disable(state_dir: str) -> None:
    from miniagent.assistant.engine.cli_commands import cmd_schedule

    save_tasks(
        [
            ScheduledTask(
                id="rm1",
                name="rm1",
                prompt="p",
                enabled=True,
                schedule=ScheduleSpec(kind="interval", interval_seconds=60),
                session=SessionSpec(mode="primary"),
                next_run_at=time.time() + 60,
            )
        ]
    )
    out = cmd_schedule("/schedule disable rm1", allow_mutations=True)
    assert "已禁用" in out
    assert load_tasks()[0].enabled is False
    out2 = cmd_schedule("/schedule enable rm1", allow_mutations=True)
    assert "已启用" in out2
    assert load_tasks()[0].enabled is True
    out3 = cmd_schedule("/schedule remove rm1", allow_mutations=True)
    assert "已删除" in out3
    assert load_tasks() == []


@pytest.mark.asyncio
async def test_tick_once_respects_disable_config(
    state_dir: str, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_test_config(
        tmp_path,
        {"paths": {"state_dir": state_dir}, "scheduled_tasks": {"disabled": True}},
    )
    save_tasks(
        [
            ScheduledTask(
                id="off",
                name="off",
                prompt="p",
                enabled=True,
                schedule=ScheduleSpec(kind="interval", interval_seconds=60),
                session=SessionSpec(mode="primary"),
                next_run_at=time.time() - 1,
            )
        ]
    )
    engine = MagicMock()
    engine.run_agent_with_thinking = AsyncMock(return_value="ok")
    ctx = minimal_tick_ctx(engine=engine)
    st = minimal_cli_state(ctx)
    patch_tick_once_locks(monkeypatch)
    await tick_once(ctx, st, [], [])
    engine.run_agent_with_thinking.assert_not_called()


@pytest.mark.asyncio
async def test_tick_once_skips_when_scheduler_lock_held(
    state_dir: str, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_test_config(
        tmp_path,
        {"paths": {"state_dir": state_dir}, "scheduled_tasks": {"disabled": False}},
    )
    save_tasks(
        [
            ScheduledTask(
                id="lk",
                name="lk",
                prompt="p",
                enabled=True,
                schedule=ScheduleSpec(kind="interval", interval_seconds=60),
                session=SessionSpec(mode="primary"),
                next_run_at=time.time() - 1,
            )
        ]
    )
    engine = MagicMock()
    engine.run_agent_with_thinking = AsyncMock(return_value="ok")
    ctx = minimal_tick_ctx(engine=engine)
    st = minimal_cli_state(ctx)
    monkeypatch.setattr(
        "miniagent.assistant.scheduled_tasks.ticker.try_acquire_scheduler_lock", lambda: False
    )
    await tick_once(ctx, st, [], [])
    engine.run_agent_with_thinking.assert_not_called()


def test_finalize_agent_error_preserves_error_and_increments_run_count(state_dir: str) -> None:
    t = ScheduledTask(
        id="ae",
        name="ae",
        prompt="p",
        schedule=ScheduleSpec(kind="interval", interval_seconds=60),
        next_run_at=50.0,
        run_count=1,
    )
    finalize_task_after_run(
        t,
        outcome="agent_error",
        agent_error="boom from agent",
        now_ts=100.0,
    )
    assert t.run_count == 2
    assert t.last_run_at == 100.0
    assert t.last_error == "boom from agent"
    assert t.next_run_at == 160.0


def test_repair_invalid_schedules_fills_missing_next_run(state_dir: str) -> None:
    t = ScheduledTask(
        id="fix",
        name="fix",
        prompt="p",
        enabled=True,
        schedule=ScheduleSpec(kind="cron", cron_expr="0 12 * * *", timezone="UTC"),
        next_run_at=None,
    )
    changed = repair_invalid_schedules([t])
    assert changed is True
    assert t.next_run_at is not None
    assert t.next_run_at > 0


def test_dispatch_failure_backoff_seconds_from_config(tmp_path) -> None:
    install_test_config(tmp_path, {"scheduled_tasks": {"dispatch_backoff": 120}})
    assert dispatch_failure_backoff_seconds() == 120


def test_cmd_schedule_update_interval(state_dir: str) -> None:
    from miniagent.assistant.engine.cli_commands import cmd_schedule

    add = cmd_schedule(
        "/schedule add upd_cli every 60 primary -- old prompt",
        allow_mutations=True,
    )
    assert "已添加" in add
    out = cmd_schedule(
        "/schedule update upd_cli every 120 primary -- new prompt text",
        allow_mutations=True,
    )
    assert "已更新" in out
    t = next(x for x in load_tasks() if x.id == "upd_cli")
    assert t.prompt == "new prompt text"
    assert t.schedule.interval_seconds == 120
    save_tasks([x for x in load_tasks() if x.id != "upd_cli"])


@pytest.mark.asyncio
async def test_save_tasks_async_roundtrip(state_dir: str) -> None:
    t = ScheduledTask(
        id="async1",
        name="async1",
        prompt="p",
        schedule=ScheduleSpec(kind="interval", interval_seconds=90),
        session=SessionSpec(mode="primary"),
    )
    await save_tasks_async([t])
    loaded = load_tasks()
    assert len(loaded) == 1
    assert loaded[0].id == "async1"
    assert loaded[0].schedule.interval_seconds == 90


def test_from_json_falls_back_on_invalid_kind_and_mode(caplog: pytest.LogCaptureFixture) -> None:
    t = ScheduledTask.from_json(
        {
            "id": "bad_enums",
            "name": "bad_enums",
            "prompt": "p",
            "schedule": {"kind": "weekly", "interval_seconds": 60},
            "session": {"mode": "shared"},
        }
    )
    assert t.schedule.kind == "interval"
    assert t.session.mode == "primary"
    assert "schedule.kind" in caplog.text
    assert "session.mode" in caplog.text


def test_compute_initial_once_past_time_returns_past_epoch(state_dir: str) -> None:
    t = ScheduledTask(
        id="past_once",
        name="past_once",
        prompt="p",
        schedule=ScheduleSpec(
            kind="once",
            once_at_iso="2000-01-01T00:00:00+00:00",
            timezone="UTC",
        ),
    )
    now = 1_700_000_000.0
    n = compute_initial_next_run(t, now_ts=now)
    assert n is not None
    assert n < now
