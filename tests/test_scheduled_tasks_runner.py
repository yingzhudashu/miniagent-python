"""build_run_scheduled_job_coro 与消息队列路由。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from miniagent.engine.cli_state import CliLoopState
from miniagent.feishu.types import FeishuConfig
from miniagent.infrastructure.message_queue import MessageQueueManager
from miniagent.scheduled_tasks.models import ScheduledTask, SessionSpec
from miniagent.scheduled_tasks.runner import build_run_scheduled_job_coro
from tests.scheduled_tasks_helpers import minimal_cli_state, minimal_tick_ctx


@pytest.mark.asyncio
async def test_build_run_scheduled_job_feishu_sets_is_feishu() -> None:
    engine = MagicMock()
    engine.run_agent_with_thinking = AsyncMock(return_value="ok")
    feishu_rt = MagicMock()
    feishu_rt.get_config.return_value = FeishuConfig(app_id="a", app_secret="b")

    ctx = minimal_tick_ctx(engine=engine)
    ctx.feishu = feishu_rt
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
    job_coro, mq_chat = build_run_scheduled_job_coro(ctx, st, task, [], [])
    assert mq_chat == "oc_chat1"
    with patch(
        "miniagent.scheduled_tasks.runner.send_scheduled_reply_to_feishu",
        new_callable=AsyncMock,
    ) as mock_send:
        await job_coro
        mock_send.assert_awaited_once()
    engine.run_agent_with_thinking.assert_awaited_once()
    assert engine.run_agent_with_thinking.await_args.kwargs.get("is_feishu") is True


@pytest.mark.asyncio
async def test_build_run_scheduled_job_non_cli_uses_dispatch_wait() -> None:
    engine = MagicMock()
    engine.run_agent_with_thinking = AsyncMock(return_value="ok")
    mq = MessageQueueManager()
    mq.dispatch_wait = AsyncMock()

    ctx = minimal_tick_ctx(engine=engine)
    ctx.message_queue = mq
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
    job_coro, mq_chat = build_run_scheduled_job_coro(ctx, st, task, [], [])
    assert mq_chat == "oc_x"
    await mq.dispatch_wait(mq_chat, job_coro)
    mq.dispatch_wait.assert_awaited_once()
