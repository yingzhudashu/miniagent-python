"""Focused test doubles for the explicitly injected memory runtime contract."""

from __future__ import annotations

import os
import tempfile
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock


def make_memory_runtime(
    *,
    store: Any | None = None,
    activity_log: Any | None = None,
    keyword_index: Any | None = None,
    context: Any | None = None,
    state_root: str = "workspaces",
) -> Any:
    """Create a coherent in-memory double for ``MemoryRuntimeProtocol``.

    Defaults implement the async methods reached by engine/executor tests.  A
    test can replace any collaborator while still passing one aggregate across
    the public boundary, mirroring the production object graph.
    """
    if store is None:
        store = MagicMock()
        store.load = AsyncMock(return_value=None)
        store.update_summary = AsyncMock()
        store.add_entry = AsyncMock()
        store.add_file = AsyncMock()
        store.record_turn = AsyncMock()
        store.flush_keyword_index_async = AsyncMock()
    if activity_log is None:
        activity_log = MagicMock()
        activity_log.log_session_start = AsyncMock()
        activity_log.log_llm_call = AsyncMock()
        activity_log.log_tool_call = AsyncMock()
        activity_log.log_final_reply = AsyncMock()
        activity_log.log_incomplete = AsyncMock()
    if keyword_index is None:
        keyword_index = MagicMock()
        keyword_index.get_stats.return_value = {"total_entries": 0}
    if context is None:
        context = MagicMock()
        context.inject_memory_to_messages = AsyncMock(return_value=([], {}))
        context.save_memory_after_turn = AsyncMock()

    return SimpleNamespace(
        state_root=state_root,
        store=store,
        activity_log=activity_log,
        keyword_index=keyword_index,
        context=context,
        dream_scheduler=SimpleNamespace(schedule=MagicMock()),
        shutdown=AsyncMock(),
        close=MagicMock(),
        remove_session_entries=MagicMock(return_value=0),
    )


def make_memory_bundle() -> tuple[Any, Any, Any, Any]:
    """Create the real isolated memory collaborators used by startup tests."""
    from miniagent.assistant.memory.activity_log import ActivityLogger
    from miniagent.assistant.memory.keyword_index import KeywordIndex
    from miniagent.assistant.memory.memory_context_service import create_default_memory_context
    from miniagent.assistant.memory.store import DefaultMemoryStore

    root = tempfile.mkdtemp()
    keyword_index = KeywordIndex(state_dir=root)
    store = DefaultMemoryStore(state_dir=root, keyword_index=keyword_index)
    activity_log = ActivityLogger(base_dir=os.path.join(root, "memory"))
    context = create_default_memory_context(store, keyword_index)
    return store, activity_log, keyword_index, context


def make_background_task_manager() -> Any:
    """Create a lifecycle-safe background task manager test double."""
    manager = MagicMock()
    manager.shutdown = AsyncMock()
    return manager


def make_knowledge_registry() -> Any:
    """Create an empty knowledge registry test double for explicit injection."""
    registry = MagicMock()
    registry.list.return_value = []
    registry.search.return_value = ""
    return registry


__all__ = [
    "make_background_task_manager",
    "make_knowledge_registry",
    "make_memory_bundle",
    "make_memory_runtime",
]
