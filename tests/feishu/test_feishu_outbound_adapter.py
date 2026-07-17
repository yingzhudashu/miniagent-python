"""Feishu standard outbound event adapter tests."""

from __future__ import annotations

import pytest

from miniagent.ui.channels import ChannelAdapter, ChannelRegistry
from miniagent.ui.feishu.outbound import (
    FeishuChannelAdapter,
    UnsupportedFeishuEventError,
    build_feishu_final_event,
    build_feishu_reply_event,
)
from miniagent.ui.messages import (
    ChannelTarget,
    OutboundEvent,
    OutboundEventKind,
)


@pytest.mark.asyncio
async def test_final_event_retains_reply_and_thread_targeting() -> None:
    """The adapter must call the sender with unchanged routing data."""
    delivered: list[tuple[str, str, str | None, bool]] = []
    adapter = FeishuChannelAdapter(
        lambda chat_id, text, reply_to, in_thread: delivered.append(
            (chat_id, text, reply_to, in_thread)
        )
    )
    registry = ChannelRegistry([adapter])
    event = build_feishu_final_event(
        "answer",
        "oc_chat",
        reply_to_message_id="om_message",
        thread_id="omt_thread",
        trace_id="om_message",
    )

    await registry.send(event)

    assert delivered == [("oc_chat", "answer", "om_message", True)]
    assert event.trace_id == "om_message"
    assert isinstance(adapter, ChannelAdapter)


@pytest.mark.asyncio
async def test_adapter_awaits_async_sender() -> None:
    """Existing async Feishu reply functions remain supported."""
    delivered: list[str] = []

    async def sender(_chat_id: str, text: str, _reply_to: str | None, _thread: bool) -> None:
        delivered.append(text)

    adapter = FeishuChannelAdapter(sender)
    await adapter.send(build_feishu_final_event("done", "oc_chat"))
    assert delivered == ["done"]


@pytest.mark.asyncio
async def test_unmigrated_kind_is_rejected_explicitly() -> None:
    """Thinking events must not accidentally use the reply-card format."""
    adapter = FeishuChannelAdapter(lambda *_args: None)
    event = OutboundEvent.create(
        kind=OutboundEventKind.THINKING_DELTA,
        target=ChannelTarget("feishu", "oc_chat"),
        content="working",
    )

    with pytest.raises(UnsupportedFeishuEventError, match="thinking_delta"):
        await adapter.send(event)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [OutboundEventKind.STATUS, OutboundEventKind.ERROR])
async def test_status_and_error_use_same_reply_sender(kind: OutboundEventKind) -> None:
    """Command and error text retain the poll_server reply presentation."""
    delivered: list[str] = []
    adapter = FeishuChannelAdapter(
        lambda _chat, text, _reply_to, _thread: delivered.append(text)
    )

    await adapter.send(build_feishu_reply_event(kind, "message", "oc_chat"))
    assert delivered == ["message"]
