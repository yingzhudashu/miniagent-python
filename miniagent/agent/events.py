"""Immutable semantic events emitted by one :class:`AgentRuntime`."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any
from uuid import uuid4


class AgentEventKind(str, Enum):
    RUN_STARTED = "run_started"
    PHASE_STARTED = "phase_started"
    PHASE_FINISHED = "phase_finished"
    THINKING_DELTA = "thinking_delta"
    THINKING_FINAL = "thinking_final"
    TOOL_STARTED = "tool_started"
    TOOL_FINISHED = "tool_finished"
    CONFIRMATION_REQUIRED = "confirmation_required"
    REFLECTION = "reflection"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    RUN_CANCELLED = "run_cancelled"


@dataclass(frozen=True, slots=True)
class AgentEvent:
    """One ordered event with correlation fields required by every UI."""

    kind: AgentEventKind
    run_id: str
    session_id: str
    trace_id: str
    sequence: int
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    payload: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    event_id: str = field(default_factory=lambda: uuid4().hex)

    def __post_init__(self) -> None:
        for name in ("run_id", "session_id", "trace_id", "event_id"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} must not be empty")
        if self.sequence < 0:
            raise ValueError("sequence must not be negative")
        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


__all__ = ["AgentEvent", "AgentEventKind"]
