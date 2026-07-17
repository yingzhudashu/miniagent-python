"""Public contracts implemented by CLI, TUI, Feishu and future UI surfaces."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4

from miniagent.agent.events import AgentEvent
from miniagent.agent.lifecycle import LifecycleService
from miniagent.ui.messages import Attachment


class UIInputKind(str, Enum):
    MESSAGE = "message"
    COMMAND = "command"
    CANCEL = "cancel"
    CONFIRMATION = "confirmation"


@dataclass(frozen=True, slots=True)
class UITarget:
    surface_id: str
    conversation_id: str
    thread_id: str | None = None
    reply_to: str | None = None

    def __post_init__(self) -> None:
        if not self.surface_id.strip() or not self.conversation_id.strip():
            raise ValueError("surface_id and conversation_id must not be empty")


@dataclass(frozen=True, slots=True)
class UIInput:
    kind: UIInputKind
    target: UITarget
    content: str = ""
    sender_id: str = ""
    session_id: str | None = None
    attachments: tuple[Attachment, ...] = ()
    metadata: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )
    idempotency_key: str | None = None
    trace_id: str | None = None
    input_id: str = field(default_factory=lambda: uuid4().hex)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not self.input_id.strip():
            raise ValueError("input_id must not be empty")
        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")
        if self.kind is not UIInputKind.CANCEL and not self.content and not self.attachments:
            raise ValueError("UI input needs content or attachments")
        object.__setattr__(self, "attachments", tuple(self.attachments))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@runtime_checkable
class UISurface(LifecycleService, Protocol):
    """Input producer and AgentEvent renderer owned by the UI layer."""

    @property
    def surface_id(self) -> str: ...

    def inputs(self) -> AsyncIterator[UIInput]: ...

    async def render(self, event: AgentEvent, target: UITarget) -> None: ...


__all__ = ["UIInput", "UIInputKind", "UISurface", "UITarget"]
