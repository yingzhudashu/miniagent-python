"""Unit tests for the platform-neutral contracts layer."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import FrozenInstanceError
from datetime import datetime

import pytest

from miniagent.contracts import (
    Attachment,
    ChannelTarget,
    HealthReport,
    HealthState,
    InboundMessage,
    OutboundEvent,
    OutboundEventKind,
)


def test_inbound_message_has_collision_safe_default_route_key() -> None:
    message = InboundMessage.create(
        channel="feishu",
        conversation_id="chat-1",
        sender_id="user-1",
        content="hello",
        metadata={"message_id": "m1"},
    )
    assert message.route_key == "feishu:chat-1"
    assert message.received_at.tzinfo is not None
    with pytest.raises(TypeError):
        message.metadata["message_id"] = "changed"  # type: ignore[index]


def test_explicit_session_key_and_attachments_are_preserved() -> None:
    attachment = Attachment("file-1", name="report.pdf", size=10)
    message = InboundMessage.create(
        channel="cli",
        conversation_id="local",
        sender_id="user",
        content="",
        session_key="default",
        attachments=[attachment],
    )
    assert message.route_key == "default"
    assert message.attachments == (attachment,)


def test_inbound_message_rejects_naive_time_and_empty_payload() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        InboundMessage.create(
            channel="cli",
            conversation_id="local",
            sender_id="user",
            content="hello",
            received_at=datetime.now(),
        )
    with pytest.raises(ValueError, match="content or attachments"):
        InboundMessage.create(
            channel="cli", conversation_id="local", sender_id="user", content=""
        )


def test_outbound_event_is_ordered_and_immutable() -> None:
    event = OutboundEvent.create(
        kind=OutboundEventKind.FINAL,
        target=ChannelTarget("feishu", "chat-1", reply_to="m1"),
        content="done",
        sequence=2,
    )
    assert event.kind is OutboundEventKind.FINAL
    assert event.sequence == 2
    with pytest.raises(FrozenInstanceError):
        event.sequence = 3  # type: ignore[misc]


def test_health_report_copies_metadata() -> None:
    source = {"queue_depth": 1}
    report = HealthReport(HealthState.READY, metadata=source)
    source["queue_depth"] = 2
    assert report.metadata["queue_depth"] == 1


def test_importing_types_does_not_eagerly_import_feishu_adapter() -> None:
    code = (
        "import sys; import miniagent.types; "
        "assert 'miniagent.feishu' not in sys.modules, sorted(sys.modules)"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
