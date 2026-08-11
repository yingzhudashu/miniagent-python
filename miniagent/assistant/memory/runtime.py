"""Construction and lifecycle ownership for the memory subsystem.

This module is a factory, not a service locator: every call to
``create_memory_runtime`` returns a new, internally consistent object graph.
The application entrypoint calls it once and stores the result in
``ApplicationContainer``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from miniagent.assistant.memory.activity_log import ActivityLogger
from miniagent.assistant.memory.dream_scheduler import DreamScheduler
from miniagent.assistant.memory.embedding_search import EmbeddingSearchProvider
from miniagent.assistant.memory.keyword_index import KeywordIndex
from miniagent.assistant.memory.layered_memory import LongTermMemoryStore
from miniagent.assistant.memory.memory_context_service import (
    DefaultMemoryContext,
    create_default_memory_context,
)
from miniagent.assistant.memory.shared_registry import MemoryEntryRegistry
from miniagent.assistant.memory.store import DefaultMemoryStore
from miniagent.assistant.state import StateStore


@dataclass(frozen=True, slots=True)
class MemoryRuntime:
    """One process-owned, internally shared memory object graph."""

    state_root: str
    state_store: StateStore
    registry: MemoryEntryRegistry
    keyword_index: KeywordIndex
    embedding_provider: EmbeddingSearchProvider
    store: DefaultMemoryStore
    activity_log: ActivityLogger
    context: DefaultMemoryContext
    longterm: LongTermMemoryStore
    dream_scheduler: DreamScheduler

    async def start(self) -> None:
        """Open durable state before any memory component serves a request."""
        await self.state_store.open()
        rows = await self.state_store.list_memory_entries(namespace="memory")
        self.registry.hydrate_entries(rows)
        await self.embedding_provider.start(self.state_store)
        self.keyword_index.load()

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

    async def remove_session_entries(self, session_key: str) -> int:
        """Delete durable session memory before evicting derived accelerators."""
        removed_keys = await self.state_store.delete_session_memory(session_key)
        self.registry.remove_session_entries(session_key)
        if not removed_keys:
            return 0
        self.keyword_index.remove_entry_keys(removed_keys)
        self.embedding_provider.index.remove_entry_keys(removed_keys)
        return len(removed_keys)


def create_memory_runtime(
    state_root: str | None = None,
    *,
    state_store: StateStore | None = None,
) -> MemoryRuntime:
    """Build a fresh memory graph rooted at the configured state directory."""
    if state_root is None:
        from miniagent.assistant.infrastructure.paths import resolve_state_dir

        state_root = resolve_state_dir()

    state_store = state_store or StateStore(state_root)
    registry = MemoryEntryRegistry()
    keyword_index = KeywordIndex(state_dir=state_root, registry=registry)
    embedding_provider = EmbeddingSearchProvider(
        state_dir=state_root,
        registry=registry,
        state_store=state_store,
    )
    store = DefaultMemoryStore(
        state_dir=state_root,
        keyword_index=keyword_index,
        embedding_provider=embedding_provider,
        state_store=state_store,
        registry=registry,
    )
    activity_log = ActivityLogger(base_dir=os.path.join(state_root, "memory"))
    context = create_default_memory_context(
        store,
        keyword_index,
        embedding_provider=embedding_provider,
    )
    longterm = LongTermMemoryStore(state_store)
    dream_scheduler = DreamScheduler(state_root, state_store, longterm)
    return MemoryRuntime(
        state_root=state_root,
        state_store=state_store,
        registry=registry,
        keyword_index=keyword_index,
        embedding_provider=embedding_provider,
        store=store,
        activity_log=activity_log,
        context=context,
        longterm=longterm,
        dream_scheduler=dream_scheduler,
    )


__all__ = ["MemoryRuntime", "create_memory_runtime"]
