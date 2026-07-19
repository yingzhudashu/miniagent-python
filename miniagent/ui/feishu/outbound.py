"""Deliver standard UI outbound events through a Feishu reply sender."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from miniagent.ui.feishu.inbound import FEISHU_CHANNEL
from miniagent.ui.messages import ChannelTarget, OutboundEvent, OutboundEventKind

FeishuReplySender = Callable[[str, str, str | None, bool], Awaitable[None] | None]


class UnsupportedFeishuEventError(ValueError):
    """The migrated Feishu sender cannot represent an outbound event kind."""


@dataclass(frozen=True, slots=True)
class FeishuChannelAdapter:
    """Channel adapter backed by an SDK-independent Feishu reply callable."""

    reply_sender: FeishuReplySender
    channel_id: str = field(default=FEISHU_CHANNEL, init=False)

    async def send(self, event: OutboundEvent) -> None:
        """Deliver supported final/status/error events to the target chat."""
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


def build_feishu_reply_event(
    kind: OutboundEventKind,
    content: str,
    chat_id: str,
    *,
    reply_to_message_id: str | None = None,
    thread_id: str | None = None,
    trace_id: str | None = None,
) -> OutboundEvent:
    """Build a normalized Feishu event while preserving reply/thread routing."""
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


def build_feishu_final_event(
    content: str,
    chat_id: str,
    *,
    reply_to_message_id: str | None = None,
    thread_id: str | None = None,
    trace_id: str | None = None,
) -> OutboundEvent:
    """Build the common final-answer specialization of a Feishu reply event."""
    return build_feishu_reply_event(
        OutboundEventKind.FINAL,
        content,
        chat_id,
        reply_to_message_id=reply_to_message_id,
        thread_id=thread_id,
        trace_id=trace_id,
    )


__all__ = [
    "FeishuChannelAdapter",
    "UnsupportedFeishuEventError",
    "build_feishu_final_event",
    "build_feishu_reply_event",
]
