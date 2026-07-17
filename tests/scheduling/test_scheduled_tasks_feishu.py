"""定时任务飞书镜像投递与时区默认。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from miniagent.assistant.infrastructure.channel_router import ChannelRouter
from miniagent.assistant.scheduled_tasks.feishu_delivery import (
    FeishuDeliveryTarget,
    resolve_feishu_delivery,
    schedule_feishu_last_chat_enabled,
    schedule_feishu_mirror_enabled,
    send_scheduled_reply_to_feishu,
)
from miniagent.assistant.scheduled_tasks.models import ScheduledTask, ScheduleSpec, SessionSpec
from miniagent.assistant.scheduled_tasks.timezone_util import default_schedule_timezone
from miniagent.ui.channels import ChannelRegistry
from miniagent.ui.messages import OutboundEvent
from tests.support.channel import FunctionChannelAdapter
from tests.support.config import install_test_config


@pytest.mark.asyncio
async def test_scheduled_feishu_reply_uses_registered_standard_adapter() -> None:
    """定时结果优先构造 FINAL 事件，避免绕过组合根通道注册表。"""
    delivered: list[OutboundEvent] = []

    async def sender(event: OutboundEvent) -> None:
        delivered.append(event)

    channels = ChannelRegistry([FunctionChannelAdapter("feishu", sender)])
    target = FeishuDeliveryTarget(
        receive_chat_id="oc_schedule",
        session_key="default",
        mq_chat_id="oc_schedule",
    )
    task = ScheduledTask(id="sched-final", name="日报", prompt="生成日报")

    await send_scheduled_reply_to_feishu(
        target,
        task,
        "完成",
        outbound_channels=channels,
    )

    assert len(delivered) == 1
    assert delivered[0].content == "[定时任务 日报]\n完成"
    assert delivered[0].target.conversation_id == "oc_schedule"
    assert delivered[0].trace_id == "sched-final"


def test_default_schedule_timezone_from_tz(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    install_test_config(tmp_path, {"timezone": {"default": ""}})
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    assert default_schedule_timezone() == "Asia/Shanghai"


def test_default_schedule_timezone_schedule_overrides_tz(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    install_test_config(
        tmp_path,
        {
            "timezone": {"default": ""},
            "scheduled_tasks": {"timezone": "Europe/London"},
        },
    )
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    assert default_schedule_timezone() == "Europe/London"


def test_schedule_feishu_mirror_disabled(tmp_path) -> None:
    install_test_config(tmp_path, {"scheduled_tasks": {"feishu_mirror": False}})
    assert schedule_feishu_mirror_enabled() is False


def test_resolve_feishu_delivery_from_bound_p2p_uses_last_chat_mq(tmp_path) -> None:
    install_test_config(tmp_path, {})
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


def test_resolve_feishu_delivery_p2p_falls_back_to_ou_without_last_chat(tmp_path) -> None:
    install_test_config(tmp_path, {})
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


def test_resolve_feishu_delivery_mirror_off(tmp_path) -> None:
    install_test_config(tmp_path, {"scheduled_tasks": {"feishu_mirror": False}})
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
        state={"feishu_enabled": True},  # type: ignore[arg-type]
        feishu_runtime=feishu_rt,
    )
    assert target is None


def test_resolve_feishu_delivery_not_running(tmp_path) -> None:
    install_test_config(tmp_path, {})
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


def test_resolve_feishu_delivery_feishu_disabled(tmp_path) -> None:
    install_test_config(tmp_path, {})
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


def test_resolve_feishu_last_chat_requires_config(tmp_path) -> None:
    install_test_config(tmp_path, {"scheduled_tasks": {"feishu_last_chat": False}})
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


def test_resolve_feishu_last_chat_with_config(tmp_path) -> None:
    install_test_config(tmp_path, {"scheduled_tasks": {"feishu_last_chat": True}})
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
async def test_runner_sends_feishu_reply_on_mirror(tmp_path) -> None:
    install_test_config(tmp_path, {"paths": {"state_dir": str(tmp_path)}})
    from miniagent.assistant.scheduled_tasks.runner import build_scheduled_job
    from miniagent.assistant.scheduled_tasks.store import save_tasks

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

    from tests.support.scheduling import minimal_tick_ctx

    ctx = minimal_tick_ctx(engine=engine)
    ctx.channel_router = router
    ctx.feishu = feishu_rt
    ctx.outbound_channels = MagicMock()
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

    job = build_scheduled_job(ctx, st, task, [], [])  # type: ignore[arg-type]
    assert job.queue_key == "oc_mirror_queue"

    with patch(
        "miniagent.assistant.scheduled_tasks.runner.send_scheduled_reply_to_feishu",
        new_callable=AsyncMock,
    ) as mock_send:
        err = await job.run(job.message)
        assert err is None
        mock_send.assert_awaited_once()
        args = mock_send.await_args
        assert "定时任务结果正文" in args[0][2]


def test_cmd_schedule_add_uses_tz_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    state_dir = str(tmp_path)
    monkeypatch.setenv("MINIAGENT_PATHS_STATE_DIR", state_dir)
    install_test_config(
        tmp_path,
        {
            "paths": {"state_dir": state_dir},
            "timezone": {"default": ""},
        },
    )
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    from miniagent.assistant.engine.commands.session_management import cmd_schedule
    from miniagent.assistant.scheduled_tasks.store import load_tasks

    msg = cmd_schedule(
        '/schedule add tzenv cron "0 12 * * *" primary -- test',
        allow_mutations=True,
    )
    assert "已添加" in msg
    t = load_tasks()[0]
    assert t.schedule.timezone == "Asia/Shanghai"
