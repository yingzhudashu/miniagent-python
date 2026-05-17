"""定时任务飞书镜像投递与时区默认。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from miniagent.infrastructure.channel_router import ChannelRouter
from miniagent.scheduled_tasks.feishu_delivery import (
    resolve_feishu_delivery,
    schedule_feishu_last_chat_enabled,
    schedule_feishu_mirror_enabled,
)
from miniagent.scheduled_tasks.models import ScheduledTask, ScheduleSpec, SessionSpec
from miniagent.scheduled_tasks.timezone_util import default_schedule_timezone


def test_default_schedule_timezone_from_tz(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MINIAGENT_SCHEDULE_TIMEZONE", raising=False)
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    assert default_schedule_timezone() == "Asia/Shanghai"


def test_default_schedule_timezone_schedule_overrides_tz(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MINIAGENT_SCHEDULE_TIMEZONE", "Europe/London")
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    assert default_schedule_timezone() == "Europe/London"


def test_schedule_feishu_mirror_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINIAGENT_SCHEDULE_FEISHU_MIRROR", "0")
    assert schedule_feishu_mirror_enabled() is False


def test_resolve_feishu_delivery_from_bound_p2p_uses_last_chat_mq() -> None:
    router = ChannelRouter()
    router.bind(router.CLI_CHANNEL, "default")
    router.set_primary("default")
    router.bind("feishu_p2p:ou_test123", "default")
    task = ScheduledTask(
        id="t",
        name="t",
        prompt="p",
        session=SessionSpec(mode="primary"),
    )
    feishu_rt = MagicMock()
    feishu_rt.is_running.return_value = True
    st = {
        "feishu_enabled": True,
        "active_session_id": "default",
        "last_feishu_receive_chat_id": "oc_p2p_chat_99",
    }
    target = resolve_feishu_delivery(
        task,
        session_key="default",
        feishu_recv=None,
        mq_chat="__cli__",
        channel_router=router,
        state=st,  # type: ignore[arg-type]
        feishu_runtime=feishu_rt,
    )
    assert target is not None
    assert target.mq_chat_id == "oc_p2p_chat_99"
    assert target.receive_chat_id == "oc_p2p_chat_99"


def test_resolve_feishu_delivery_p2p_falls_back_to_ou_without_last_chat() -> None:
    router = ChannelRouter()
    router.bind("feishu_p2p:ou_only", "default")
    task = ScheduledTask(id="t", name="t", prompt="p", session=SessionSpec(mode="primary"))
    feishu_rt = MagicMock()
    feishu_rt.is_running.return_value = True
    target = resolve_feishu_delivery(
        task,
        session_key="default",
        feishu_recv=None,
        mq_chat="__cli__",
        channel_router=router,
        state={"feishu_enabled": True},  # type: ignore[arg-type]
        feishu_runtime=feishu_rt,
    )
    assert target is not None
    assert target.mq_chat_id == "ou_only"
    assert target.receive_chat_id == "ou_only"


def test_resolve_feishu_delivery_mirror_off() -> None:
    router = ChannelRouter()
    router.bind("feishu_p2p:ou_x", "default")
    task = ScheduledTask(id="t", name="t", prompt="p", session=SessionSpec(mode="primary"))
    feishu_rt = MagicMock()
    feishu_rt.is_running.return_value = True
    with patch.dict("os.environ", {"MINIAGENT_SCHEDULE_FEISHU_MIRROR": "0"}):
        target = resolve_feishu_delivery(
            task,
            session_key="default",
            feishu_recv=None,
            mq_chat="__cli__",
            channel_router=router,
            state={"feishu_enabled": True},  # type: ignore[arg-type]
            feishu_runtime=feishu_rt,
        )
    assert target is None


def test_resolve_feishu_delivery_not_running() -> None:
    router = ChannelRouter()
    router.bind("feishu_p2p:ou_x", "default")
    task = ScheduledTask(id="t", name="t", prompt="p", session=SessionSpec(mode="primary"))
    feishu_rt = MagicMock()
    feishu_rt.is_running.return_value = False
    target = resolve_feishu_delivery(
        task,
        session_key="default",
        feishu_recv=None,
        mq_chat="__cli__",
        channel_router=router,
        state={"feishu_enabled": True},  # type: ignore[arg-type]
        feishu_runtime=feishu_rt,
    )
    assert target is None


def test_resolve_feishu_delivery_feishu_disabled() -> None:
    router = ChannelRouter()
    router.bind("feishu_p2p:ou_x", "default")
    task = ScheduledTask(id="t", name="t", prompt="p", session=SessionSpec(mode="primary"))
    feishu_rt = MagicMock()
    feishu_rt.is_running.return_value = True
    target = resolve_feishu_delivery(
        task,
        session_key="default",
        feishu_recv=None,
        mq_chat="__cli__",
        channel_router=router,
        state={"feishu_enabled": False},  # type: ignore[arg-type]
        feishu_runtime=feishu_rt,
    )
    assert target is None


def test_resolve_feishu_last_chat_requires_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MINIAGENT_SCHEDULE_FEISHU_LAST_CHAT", raising=False)
    assert schedule_feishu_last_chat_enabled() is False
    router = ChannelRouter()
    task = ScheduledTask(id="t", name="t", prompt="p", session=SessionSpec(mode="primary"))
    feishu_rt = MagicMock()
    feishu_rt.is_running.return_value = True
    target = resolve_feishu_delivery(
        task,
        session_key="default",
        feishu_recv=None,
        mq_chat="__cli__",
        channel_router=router,
        state={
            "feishu_enabled": True,
            "last_feishu_receive_chat_id": "oc_last",
        },  # type: ignore[arg-type]
        feishu_runtime=feishu_rt,
    )
    assert target is None


def test_resolve_feishu_last_chat_with_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINIAGENT_SCHEDULE_FEISHU_LAST_CHAT", "1")
    router = ChannelRouter()
    task = ScheduledTask(id="t", name="t", prompt="p", session=SessionSpec(mode="primary"))
    feishu_rt = MagicMock()
    feishu_rt.is_running.return_value = True
    target = resolve_feishu_delivery(
        task,
        session_key="default",
        feishu_recv=None,
        mq_chat="__cli__",
        channel_router=router,
        state={
            "feishu_enabled": True,
            "last_feishu_receive_chat_id": "oc_last",
        },  # type: ignore[arg-type]
        feishu_runtime=feishu_rt,
    )
    assert target is not None
    assert target.mq_chat_id == "oc_last"


@pytest.mark.asyncio
async def test_runner_sends_feishu_reply_on_mirror(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("MINI_AGENT_STATE", str(tmp_path))
    from miniagent.scheduled_tasks.runner import build_run_scheduled_job_coro
    from miniagent.scheduled_tasks.store import save_tasks

    router = ChannelRouter()
    router.bind(router.CLI_CHANNEL, "default")
    router.set_primary("default")
    router.bind("feishu_p2p:ou_sched", "default")

    task = ScheduledTask(
        id="fs1",
        name="fs1",
        prompt="ping",
        enabled=True,
        schedule=ScheduleSpec(kind="interval", interval_seconds=3600),
        session=SessionSpec(mode="primary"),
    )
    save_tasks([task])

    engine = MagicMock()
    engine.run_agent_with_thinking = AsyncMock(return_value="定时任务结果正文")

    feishu_rt = MagicMock()
    feishu_rt.is_running.return_value = True
    feishu_rt.get_config.return_value = SimpleNamespace(app_id="x", app_secret="y")

    ctx = SimpleNamespace(
        message_queue=MagicMock(),
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
    st = {
        "active_session_id": "default",
        "skill_toolboxes": [],
        "skill_prompts": [],
        "feishu_enabled": True,
        "session_manager": None,
        "instance_id": 1,
        "runtime_ctx": ctx,
        "feishu_p2p_synced_senders": set(),
        "last_feishu_receive_chat_id": "oc_mirror_queue",
    }

    coro, mq_chat = build_run_scheduled_job_coro(ctx, st, task, [], [])  # type: ignore[arg-type]
    assert mq_chat == "oc_mirror_queue"

    with patch(
        "miniagent.scheduled_tasks.runner.send_scheduled_reply_to_feishu",
        new_callable=AsyncMock,
    ) as mock_send:
        err = await coro
        assert err is None
        mock_send.assert_awaited_once()
        args = mock_send.await_args
        assert "定时任务结果正文" in args[0][3]


def test_cmd_schedule_add_uses_tz_env(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("MINI_AGENT_STATE", str(tmp_path))
    monkeypatch.delenv("MINIAGENT_SCHEDULE_TIMEZONE", raising=False)
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    from miniagent.engine.cli_commands import cmd_schedule
    from miniagent.scheduled_tasks.store import load_tasks

    msg = cmd_schedule(
        '.schedule add tzenv cron "0 12 * * *" primary -- test',
        allow_mutations=True,
    )
    assert "已添加" in msg
    t = load_tasks()[0]
    assert t.schedule.timezone == "Asia/Shanghai"
