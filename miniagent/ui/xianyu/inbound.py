"""Pure Xianyu protocol-to-channel message normalization."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlparse

from miniagent.ui.messages import Attachment, InboundMessage

XIANYU_CHANNEL = "xianyu"
_ALLOWED_IMAGE_SUFFIXES = (
    ".alicdn.com",
    ".goofish.com",
    ".mmcdn.cn",
    ".taobao.com",
    ".tbcdn.cn",
)


@dataclass(frozen=True, slots=True)
class XianyuInbound:
    """Normalized text or image message emitted by the Xianyu protocol layer."""

    message_id: str
    conversation_id: str
    sender_id: str
    sender_name: str
    kind: Literal["text", "image"]
    text: str = ""
    image_url: str = ""
    occurred_at_ms: int = 0
    item_id: str = ""


def received_at(milliseconds: int) -> datetime:
    """Convert a platform timestamp to an aware UTC datetime."""
    if milliseconds <= 0:
        return datetime.now(timezone.utc)
    return datetime.fromtimestamp(milliseconds / 1000, timezone.utc)


def allowed_image_url(value: str) -> bool:
    """Accept only HTTPS URLs hosted by an allowed Alibaba CDN suffix."""
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and bool(host) and any(
        host == suffix[1:] or host.endswith(suffix) for suffix in _ALLOWED_IMAGE_SUFFIXES
    )


def normalize_message(
    inbound: XianyuInbound,
    *,
    attachment: Attachment | None = None,
) -> InboundMessage:
    """Build the channel-neutral inbound message without side effects."""
    content = inbound.text.strip()
    if inbound.item_id:
        content = f"当前商品 ID: {inbound.item_id}\n买家消息: {content}"
    attachments: tuple[Attachment, ...] = ()
    if inbound.kind == "image":
        if attachment is None:
            raise ValueError("image inbound messages require a downloaded attachment")
        attachments = (attachment,)
        content = f"买家发送了一张图片，请使用 analyze_image 分析：{attachment.local_path}"
    return InboundMessage.create(
        event_id=inbound.message_id,
        channel=XIANYU_CHANNEL,
        conversation_id=inbound.conversation_id,
        sender_id=inbound.sender_id,
        content=content,
        received_at=received_at(inbound.occurred_at_ms),
        session_key=f"xianyu:{inbound.conversation_id}",
        attachments=attachments,
        idempotency_key=inbound.message_id,
        trace_id=inbound.message_id,
        metadata={
            "message_id": inbound.message_id,
            "sender_name": inbound.sender_name,
            "item_id": inbound.item_id,
            "kind": inbound.kind,
        },
    )


__all__ = [
    "XIANYU_CHANNEL",
    "XianyuInbound",
    "allowed_image_url",
    "normalize_message",
    "received_at",
]
