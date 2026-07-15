"""Adapter from standard outbound events to Feishu reply senders."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from miniagent.assistant.contracts.messages import ChannelTarget, OutboundEvent, OutboundEventKind
from miniagent.assistant.feishu.inbound_adapter import FEISHU_CHANNEL

FeishuReplySender = Callable[[str, str, str | None, bool], Awaitable[None] | None]


class UnsupportedFeishuEventError(ValueError):
    """The Feishu sender cannot represent an event kind's behavior."""


@dataclass(frozen=True, slots=True)
class FeishuChannelAdapter:
    """Deliver migrated reply events through the existing card/text sender."""

    reply_sender: FeishuReplySender
    channel_id: str = field(default=FEISHU_CHANNEL, init=False)

    async def send(self, event: OutboundEvent) -> None:
        """Project supported reply events onto the Feishu sender signature."""
        if event.kind not in {
            OutboundEventKind.FINAL,
            OutboundEventKind.STATUS,
            OutboundEventKind.ERROR,
        }:
            raise UnsupportedFeishuEventError(
                f"Feishu event kind {event.kind.value!r} has no migrated sender"
            )
        result = self.reply_sender(
            event.target.conversation_id,
            event.content,
            event.target.reply_to,
            bool(event.target.thread_id),
        )
        if inspect.isawaitable(result):
            await result


def build_feishu_final_event(
    content: str,
    chat_id: str,
    *,
    reply_to_message_id: str | None = None,
    thread_id: str | None = None,
    trace_id: str | None = None,
) -> OutboundEvent:
    """Build a final Feishu event retaining reply and thread targeting."""
    return build_feishu_reply_event(
        OutboundEventKind.FINAL,
        content,
        chat_id,
        reply_to_message_id=reply_to_message_id,
        thread_id=thread_id,
        trace_id=trace_id,
    )


def build_feishu_reply_event(
    kind: OutboundEventKind,
    content: str,
    chat_id: str,
    *,
    reply_to_message_id: str | None = None,
    thread_id: str | None = None,
    trace_id: str | None = None,
) -> OutboundEvent:
    """Build one Feishu reply event with stable target metadata."""
    return OutboundEvent.create(
        kind=kind,
        target=ChannelTarget(
            channel=FEISHU_CHANNEL,
            conversation_id=chat_id,
            thread_id=(thread_id or "").strip() or None,
            reply_to=(reply_to_message_id or "").strip() or None,
        ),
        content=content,
        trace_id=(trace_id or "").strip() or None,
    )


__all__ = [
    "FeishuChannelAdapter",
    "UnsupportedFeishuEventError",
    "build_feishu_final_event",
    "build_feishu_reply_event",
]
