"""Construction and lifecycle ownership for the memory subsystem.

This module is a factory, not a service locator: every call to
``create_memory_runtime`` returns a new, internally consistent object graph.
The application entrypoint calls it once and stores the result in
``ApplicationContainer``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from miniagent.memory.activity_log import ActivityLogger
from miniagent.memory.dream_scheduler import DreamScheduler
from miniagent.memory.embedding_search import EmbeddingSearchProvider
from miniagent.memory.keyword_index import KeywordIndex
from miniagent.memory.memory_context_service import (
    DefaultMemoryContext,
    create_default_memory_context,
)
from miniagent.memory.shared_registry import MemoryEntryRegistry
from miniagent.memory.store import DefaultMemoryStore


@dataclass(frozen=True, slots=True)
class MemoryRuntime:
    """One process-owned, internally shared memory object graph."""

    state_root: str
    registry: MemoryEntryRegistry
    keyword_index: KeywordIndex
    embedding_provider: EmbeddingSearchProvider
    store: DefaultMemoryStore
    activity_log: ActivityLogger
    context: DefaultMemoryContext
    dream_scheduler: DreamScheduler

    async def shutdown(self) -> None:
        """Stop maintenance and close network resources owned by this runtime."""
        try:
            await self.dream_scheduler.shutdown()
        finally:
            await self.embedding_provider.close()

    def close(self) -> None:
        """Persist the source registry and every derived index.

        Writes are idempotent; individual implementations skip disk I/O when
        clean.  Exceptions intentionally propagate so the application shutdown
        coordinator can report the failing resource while continuing cleanup.
        """
        self.keyword_index.save()
        self.registry.save()
        self.embedding_provider.index.save()

    def remove_session_entries(self, session_key: str) -> int:
        """Remove a session atomically from the registry and both search indexes."""
        removed_keys = self.registry.remove_session_entries(session_key)
        if not removed_keys:
            return 0
        self.keyword_index.remove_entry_keys(removed_keys)
        self.embedding_provider.index.remove_entry_keys(removed_keys)
        return len(removed_keys)


def create_memory_runtime(state_root: str | None = None) -> MemoryRuntime:
    """Build a fresh memory graph rooted at the configured state directory."""
    if state_root is None:
        from miniagent.infrastructure.paths import resolve_state_dir

        state_root = resolve_state_dir()

    registry = MemoryEntryRegistry(state_dir=state_root)
    keyword_index = KeywordIndex(state_dir=state_root, registry=registry)
    embedding_provider = EmbeddingSearchProvider(state_dir=state_root, registry=registry)
    store = DefaultMemoryStore(
        state_dir=state_root,
        keyword_index=keyword_index,
        embedding_provider=embedding_provider,
    )
    activity_log = ActivityLogger(base_dir=os.path.join(state_root, "memory"))
    context = create_default_memory_context(
        store,
        keyword_index,
        embedding_provider=embedding_provider,
    )
    dream_scheduler = DreamScheduler(state_root)
    return MemoryRuntime(
        state_root=state_root,
        registry=registry,
        keyword_index=keyword_index,
        embedding_provider=embedding_provider,
        store=store,
        activity_log=activity_log,
        context=context,
        dream_scheduler=dream_scheduler,
    )


__all__ = ["MemoryRuntime", "create_memory_runtime"]
