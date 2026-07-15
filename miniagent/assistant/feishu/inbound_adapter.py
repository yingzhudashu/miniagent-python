"""Map Feishu SDK-neutral payloads into platform-neutral inbound contracts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from miniagent.assistant.contracts.messages import Attachment, InboundMessage
from miniagent.assistant.feishu.types import FeishuInboundText

FEISHU_CHANNEL = "feishu"


def _received_at(create_time: int) -> datetime | None:
    """Convert the validated Feishu epoch seconds when one is available."""
    if create_time <= 0:
        return None
    try:
        return datetime.fromtimestamp(create_time, timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def build_feishu_inbound_message(
    inbound: FeishuInboundText,
    session_key: str,
) -> InboundMessage:
    """Normalize one routed Feishu text event without changing transport policy."""
    message_id = (inbound.message_id or "").strip()
    chat_type = (inbound.chat_type or "group").strip().lower() or "group"
    received_at = _received_at(inbound.create_time)
    kwargs: dict[str, Any] = {
        "channel": FEISHU_CHANNEL,
        "conversation_id": inbound.chat_id,
        "sender_id": inbound.sender_id,
        "content": inbound.text,
        "session_key": session_key,
        "thread_id": (inbound.thread_id or "").strip() or None,
        "reply_to": (inbound.parent_id or "").strip() or None,
        "idempotency_key": message_id or None,
        "trace_id": message_id or None,
        "metadata": {
            "chat_type": chat_type,
            "message_id": message_id,
            "root_id": (inbound.root_id or "").strip() or None,
            "parent_id": (inbound.parent_id or "").strip() or None,
            "create_time": inbound.create_time,
        },
    }
    if received_at is not None:
        kwargs["received_at"] = received_at
    return InboundMessage.create(event_id=message_id or None, **kwargs)


def build_feishu_media_inbound_message(
    *,
    content: str,
    session_key: str,
    message_id: str,
    chat_id: str,
    sender_id: str,
    chat_type: str,
    msg_type: str,
    file_key: str,
    resource_type: str,
    name: str,
    mime_type: str,
    size: int,
    local_path: str,
    relative_path: str,
    thread_id: str | None = None,
) -> InboundMessage:
    """Normalize one downloaded Feishu resource as an attachment message."""
    normalized_message_id = (message_id or "").strip()
    attachment_id = (
        (file_key or "").strip()
        or normalized_message_id
        or (relative_path or "").strip()
    )
    attachment = Attachment(
        attachment_id=attachment_id,
        name=name,
        mime_type=mime_type,
        size=size,
        local_path=local_path,
        metadata={
            "relative_path": relative_path,
            "file_key": (file_key or "").strip(),
            "resource_type": resource_type,
            "msg_type": msg_type,
        },
    )
    return InboundMessage.create(
        event_id=normalized_message_id or None,
        channel=FEISHU_CHANNEL,
        conversation_id=chat_id,
        sender_id=sender_id,
        content=content,
        session_key=session_key,
        thread_id=(thread_id or "").strip() or None,
        attachments=(attachment,),
        idempotency_key=normalized_message_id or None,
        trace_id=normalized_message_id or None,
        metadata={
            "chat_type": (chat_type or "group").strip().lower() or "group",
            "message_id": normalized_message_id,
            "msg_type": msg_type,
            "resource_type": resource_type,
        },
    )


__all__ = [
    "FEISHU_CHANNEL",
    "build_feishu_inbound_message",
    "build_feishu_media_inbound_message",
]
