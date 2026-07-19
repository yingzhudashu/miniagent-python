"""Feishu text payload to standard inbound contract mapping tests."""

from __future__ import annotations

from datetime import datetime, timezone

from miniagent.ui.feishu.inbound import (
    build_feishu_inbound_message,
    build_feishu_media_inbound_message,
)
from miniagent.ui.feishu.types import FeishuInboundText


def test_feishu_text_mapping_retains_routing_and_thread_metadata() -> None:
    """Transport identifiers survive normalization without SDK objects."""
    inbound = FeishuInboundText(
        text="hello",
        chat_id="oc_chat",
        sender_id="ou_sender",
        chat_type="P2P",
        message_id="om_message",
        root_id="om_root",
        parent_id="om_parent",
        thread_id="omt_thread",
        create_time=1_700_000_000,
    )

    message = build_feishu_inbound_message(inbound, "session-1")

    assert message.event_id == "om_message"
    assert message.channel == "feishu"
    assert message.conversation_id == "oc_chat"
    assert message.sender_id == "ou_sender"
    assert message.content == "hello"
    assert message.session_key == "session-1"
    assert message.thread_id == "omt_thread"
    assert message.reply_to == "om_parent"
    assert message.idempotency_key == "om_message"
    assert message.trace_id == "om_message"
    assert message.received_at == datetime.fromtimestamp(1_700_000_000, timezone.utc)
    assert message.metadata == {
        "chat_type": "p2p",
        "message_id": "om_message",
        "root_id": "om_root",
        "parent_id": "om_parent",
        "create_time": 1_700_000_000,
    }


def test_missing_message_id_and_invalid_time_use_safe_defaults() -> None:
    """Card callbacks and malformed timestamps still receive generated identity."""
    inbound = FeishuInboundText(
        text="card action",
        chat_id="oc_chat",
        sender_id="ou_sender",
        chat_type="",
        create_time=10**30,
    )

    message = build_feishu_inbound_message(inbound, "session-2")

    assert message.event_id
    assert message.event_id != message.idempotency_key
    assert message.idempotency_key is None
    assert message.metadata["chat_type"] == "group"
    assert message.received_at.tzinfo is not None


def test_lark_millisecond_timestamp_is_normalized_to_utc_seconds() -> None:
    """Lark's wire timestamp must not overflow or bypass age-based routing checks."""
    inbound = FeishuInboundText(
        text="hello",
        chat_id="oc_chat",
        sender_id="ou_sender",
        chat_type="group",
        create_time=1_700_000_000_123,
    )

    message = build_feishu_inbound_message(inbound, "session-ms")

    assert message.received_at == datetime.fromtimestamp(1_700_000_000.123, timezone.utc)


def test_media_mapping_builds_immutable_attachment_contract() -> None:
    """Downloaded media becomes a standard attachment with workspace metadata."""
    message = build_feishu_media_inbound_message(
        content="saved image",
        session_key="session-media",
        message_id="om_media",
        chat_id="oc_chat",
        sender_id="ou_sender",
        chat_type="group",
        msg_type="image",
        file_key="img_key",
        resource_type="image",
        name="photo.png",
        mime_type="image/png",
        size=128,
        local_path="C:/workspace/feishu_incoming/photo.png",
        relative_path="feishu_incoming/photo.png",
        thread_id="omt_thread",
    )

    assert message.event_id == "om_media"
    assert message.content == "saved image"
    assert message.thread_id == "omt_thread"
    assert message.trace_id == "om_media"
    assert len(message.attachments) == 1
    attachment = message.attachments[0]
    assert attachment.attachment_id == "img_key"
    assert attachment.name == "photo.png"
    assert attachment.mime_type == "image/png"
    assert attachment.size == 128
    assert attachment.metadata == {
        "relative_path": "feishu_incoming/photo.png",
        "file_key": "img_key",
        "resource_type": "image",
        "msg_type": "image",
    }
