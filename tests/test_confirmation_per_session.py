"""Per-session ConfirmationChannel 测试。"""

from __future__ import annotations

import asyncio

import pytest

from miniagent.core.confirmation_channel import ConfirmationChannel
from miniagent.engine.engine import UnifiedEngine
from miniagent.types.confirmation import ConfirmationRequest, ConfirmationResult, ConfirmationStage


@pytest.mark.asyncio
async def test_two_sessions_pending_independently() -> None:
    engine = UnifiedEngine()
    c1 = engine.get_confirmation_channel("session_a")
    c2 = engine.get_confirmation_channel("session_b")
    assert c1 is not c2

    async def wait_a() -> None:
        req = ConfirmationRequest(stage=ConfirmationStage.CLARIFICATION, content="Q1")
        result = await c1.request_confirmation(req)
        assert result.approved is True

    async def wait_b() -> None:
        req = ConfirmationRequest(stage=ConfirmationStage.CLARIFICATION, content="Q2")
        result = await c2.request_confirmation(req)
        assert result.approved is True

    t1 = asyncio.create_task(wait_a())
    t2 = asyncio.create_task(wait_b())
    await asyncio.sleep(0.02)
    c1.respond(ConfirmationResult(approved=True, adjustment="ans1"))
    c2.respond(ConfirmationResult(approved=True, adjustment="ans2"))
    await asyncio.gather(t1, t2)


def test_confirmation_channel_isolated_instances() -> None:
    ch_a = ConfirmationChannel()
    ch_b = ConfirmationChannel()
    assert ch_a is not ch_b


def test_resolve_feishu_confirmation_channel_routes_by_session() -> None:
    from unittest.mock import MagicMock

    from miniagent.feishu import poll_server as ps

    engine = UnifiedEngine()
    router = MagicMock()
    router.resolve_feishu_message.side_effect = (
        lambda cid, sid, ct: f"feishu:{cid}"
    )
    state = ps.FeishuPollState()
    state.bind_confirmation(engine, router)

    c_a = ps._resolve_feishu_confirmation_channel(state, "oc_a", "ou_x", "group")
    c_b = ps._resolve_feishu_confirmation_channel(state, "oc_b", "ou_y", "group")
    assert c_a is not c_b
    assert c_a is engine.get_confirmation_channel("feishu:oc_a")
    assert not c_b.has_pending
