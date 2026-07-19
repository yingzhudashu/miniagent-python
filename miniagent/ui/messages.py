"""Channel-neutral immutable input and output messages."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any
from uuid import uuid4


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _frozen_metadata(value: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
    return MappingProxyType(dict(value or {}))


@dataclass(frozen=True, slots=True)
class Attachment:
    """Immutable attachment identity plus local/remote location metadata."""

    attachment_id: str
    name: str = ""
    mime_type: str = "application/octet-stream"
    size: int | None = None
    local_path: str | None = None
    remote_url: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=_frozen_metadata)

    def __post_init__(self) -> None:
        if not self.attachment_id.strip():
            raise ValueError("attachment_id must not be empty")
        if self.size is not None and self.size < 0:
            raise ValueError("attachment size must not be negative")
        object.__setattr__(self, "metadata", _frozen_metadata(self.metadata))


@dataclass(frozen=True, slots=True)
class ChannelTarget:
    """Channel-neutral destination for one outbound event."""

    channel: str
    conversation_id: str
    thread_id: str | None = None
    reply_to: str | None = None

    def __post_init__(self) -> None:
        if not self.channel.strip():
            raise ValueError("channel must not be empty")
        if not self.conversation_id.strip():
            raise ValueError("conversation_id must not be empty")


@dataclass(frozen=True, slots=True)
class InboundMessage:
    """Validated, immutable message accepted by the application layer."""

    event_id: str
    channel: str
    conversation_id: str
    sender_id: str
    content: str
    received_at: datetime
    session_key: str | None = None
    thread_id: str | None = None
    reply_to: str | None = None
    attachments: tuple[Attachment, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=_frozen_metadata)
    idempotency_key: str | None = None
    trace_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("event_id", "channel", "conversation_id", "sender_id"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} must not be empty")
        if self.received_at.tzinfo is None:
            raise ValueError("received_at must be timezone-aware")
        if not self.content and not self.attachments:
            raise ValueError("an inbound message needs content or attachments")
        object.__setattr__(self, "attachments", tuple(self.attachments))
        object.__setattr__(self, "metadata", _frozen_metadata(self.metadata))

    @property
    def route_key(self) -> str:
        """Return the explicit session key or a collision-safe channel key."""
        return self.session_key or f"{self.channel}:{self.conversation_id}"

    @classmethod
    def create(
        cls,
        *,
        channel: str,
        conversation_id: str,
        sender_id: str,
        content: str,
        event_id: str | None = None,
        received_at: datetime | None = None,
        **kwargs: Any,
    ) -> InboundMessage:
        """Construct a message with generated identity and UTC receipt time."""
        return cls(
            event_id=event_id or uuid4().hex,
            channel=channel,
            conversation_id=conversation_id,
            sender_id=sender_id,
            content=content,
            received_at=received_at or _utc_now(),
            **kwargs,
        )


class OutboundEventKind(str, Enum):
    """Stable presentation intents supported by channel adapters."""

    STATUS = "status"
    THINKING_DELTA = "thinking_delta"
    THINKING_FINAL = "thinking_final"
    CONFIRMATION = "confirmation"
    FINAL = "final"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class OutboundEvent:
    """Immutable, ordered output destined for one channel target."""

    event_id: str
    kind: OutboundEventKind
    target: ChannelTarget
    content: str
    occurred_at: datetime
    sequence: int = 0
    metadata: Mapping[str, Any] = field(default_factory=_frozen_metadata)
    idempotency_key: str | None = None
    trace_id: str | None = None

    def __post_init__(self) -> None:
        if not self.event_id.strip():
            raise ValueError("event_id must not be empty")
        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")
        if self.sequence < 0:
            raise ValueError("sequence must not be negative")
        object.__setattr__(self, "metadata", _frozen_metadata(self.metadata))

    @classmethod
    def create(
        cls,
        *,
        kind: OutboundEventKind,
        target: ChannelTarget,
        content: str,
        event_id: str | None = None,
        occurred_at: datetime | None = None,
        **kwargs: Any,
    ) -> OutboundEvent:
        """Construct an event with generated identity and UTC occurrence time."""
        return cls(
            event_id=event_id or uuid4().hex,
            kind=kind,
            target=target,
            content=content,
            occurred_at=occurred_at or _utc_now(),
            **kwargs,
        )


__all__ = [
    "Attachment",
    "ChannelTarget",
    "InboundMessage",
    "OutboundEvent",
    "OutboundEventKind",
]
