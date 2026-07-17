"""ScheduledJob construction and queue routing."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from miniagent.assistant.engine.cli_state import CliLoopState
from miniagent.assistant.infrastructure.message_queue import MessageQueueManager
from miniagent.assistant.scheduled_tasks.models import ScheduledTask, SessionSpec
from miniagent.assistant.scheduled_tasks.runner import (
    SCHEDULER_CHANNEL,
    build_scheduled_job,
)
from miniagent.ui.feishu.types import FeishuConfig
from tests.scheduled_tasks_helpers import minimal_cli_state, minimal_tick_ctx


@pytest.mark.asyncio
async def test_build_scheduled_job_exposes_standard_inbound_message() -> None:
    """Ticker receives a normalized message while preserving the Agent prompt."""
    engine = MagicMock()
    engine.run_agent_with_thinking = AsyncMock(return_value="ok")
    ctx = minimal_tick_ctx(engine=engine)
    st = minimal_cli_state(ctx)
    task = ScheduledTask(
        id="std1",
        name="standard",
        prompt="ping",
        next_run_at=123.5,
        session=SessionSpec(mode="primary"),
    )

    job = build_scheduled_job(ctx, st, task, [], [])

    assert job.message.channel == SCHEDULER_CHANNEL
    assert job.message.conversation_id == "__cli__"
    assert job.message.sender_id == "scheduler"
    assert job.message.content == "ping"
    assert job.message.session_key == "default"
    assert job.message.idempotency_key == "std1:123.500000"
    assert job.message.metadata["queue_key"] == "__cli__"
    await job.run(job.message)
    assert "ping" in engine.run_agent_with_thinking.await_args.args[0]


@pytest.mark.asyncio
async def test_build_scheduled_job_feishu_sets_is_feishu() -> None:
    engine = MagicMock()
    engine.run_agent_with_thinking = AsyncMock(return_value="ok")
    feishu_rt = MagicMock()
    feishu_rt.get_config.return_value = FeishuConfig(app_id="a", app_secret="b")

    ctx = minimal_tick_ctx(engine=engine)
    ctx.feishu = feishu_rt
    ctx.outbound_channels = MagicMock()
    st: CliLoopState = {
        **minimal_cli_state(ctx),
        "feishu_enabled": True,
    }

    task = ScheduledTask(
        id="fs1",
        name="fs1",
        prompt="hello",
        session=SessionSpec(
            mode="fixed",
            session_id="feishu:oc_chat1",
            feishu_chat_id="oc_chat1",
        ),
    )
    job = build_scheduled_job(ctx, st, task, [], [])
    assert job.queue_key == "oc_chat1"
    with patch(
        "miniagent.assistant.scheduled_tasks.runner.send_scheduled_reply_to_feishu",
        new_callable=AsyncMock,
    ) as mock_send:
        await job.run(job.message)
        mock_send.assert_awaited_once()
    engine.run_agent_with_thinking.assert_awaited_once()
    assert engine.run_agent_with_thinking.await_args.kwargs.get("is_feishu") is True


@pytest.mark.asyncio
async def test_scheduled_job_can_be_dispatched_to_resolved_queue() -> None:
    engine = MagicMock()
    engine.run_agent_with_thinking = AsyncMock(return_value="ok")
    mq = MessageQueueManager()
    mq.dispatch_wait = AsyncMock()

    ctx = minimal_tick_ctx(engine=engine)
    ctx.message_queue = mq
    ctx.outbound_channels = MagicMock()
    st = minimal_cli_state(ctx)

    task = ScheduledTask(
        id="mq1",
        name="mq1",
        prompt="p",
        session=SessionSpec(
            mode="fixed",
            session_id="feishu:oc_x",
            feishu_chat_id="oc_x",
        ),
    )
    job = build_scheduled_job(ctx, st, task, [], [])
    assert job.queue_key == "oc_x"
    await mq.dispatch_wait(job.queue_key, job.run(job.message))
    mq.dispatch_wait.assert_awaited_once()
