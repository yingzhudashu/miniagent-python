"""Lifecycle contracts for services assembled by the application bootstrap."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable


class HealthState(str, Enum):
    """Service health state used by startup, diagnostics and shutdown."""

    STARTING = "starting"
    READY = "ready"
    DEGRADED = "degraded"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class HealthReport:
    """Immutable health snapshot returned by lifecycle services."""

    state: HealthState
    detail: str = ""
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        """Copy metadata so callers cannot mutate a published health snapshot."""
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@runtime_checkable
class LifecycleService(Protocol):
    """Async service lifecycle implemented by concrete infrastructure adapters."""

    @property
    def name(self) -> str:
        """Return a stable diagnostic service name."""
        ...

    async def initialize(self) -> None:
        """Prepare local state without accepting external work."""
        ...

    async def start(self) -> None:
        """Start accepting or producing work."""
        ...

    async def stop(self) -> None:
        """Stop work and release resources; repeated calls should be safe."""
        ...

    def health(self) -> HealthReport:
        """Return the current non-blocking service health snapshot."""
        ...


__all__ = ["HealthReport", "HealthState", "LifecycleService"]
