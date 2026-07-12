"""Application-facing contract for the process-owned memory subsystem."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class MemoryRuntimeProtocol(Protocol):
    """Cohesive memory services owned by one application process.

    Callers receive this aggregate from the composition root instead of locating
    stores or indexes through module globals.  The aggregate also owns durable
    flushes and cross-index removal, so lifecycle and consistency stay together.
    """

    state_root: str
    # ``Any`` keeps the innermost contracts package independent from the
    # implementation-oriented ``types`` package.  Concrete boundaries retain
    # their precise protocols; this aggregate specifies ownership and shape.
    store: Any
    activity_log: Any
    keyword_index: Any
    context: Any
    dream_scheduler: Any

    async def shutdown(self) -> None:
        """Stop asynchronous maintenance before durable close."""
        ...

    def close(self) -> None:
        """Persist all dirty memory indexes before process shutdown."""
        ...

    def remove_session_entries(self, session_key: str) -> int:
        """Remove one session from the shared registry and derived indexes."""
        ...


__all__ = ["MemoryRuntimeProtocol"]
