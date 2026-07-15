"""Tests for the explicitly owned memory runtime object graph."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock

import pytest

from miniagent.agent.types.memory import MemoryEntryInput
from miniagent.assistant.memory.runtime import create_memory_runtime


def test_runtime_uses_one_state_root_and_shared_registry(tmp_path) -> None:
    state_root = str(tmp_path / "state")
    runtime = create_memory_runtime(state_root)

    assert runtime.state_root == state_root
    assert runtime.store._state_dir == state_root
    assert runtime.keyword_index._state_dir == state_root
    assert os.path.normpath(runtime.activity_log._base_dir) == os.path.normpath(
        os.path.join(state_root, "memory")
    )
    assert runtime.keyword_index._registry is runtime.registry
    assert runtime.embedding_provider._registry is runtime.registry


def test_each_factory_call_returns_an_independent_graph(tmp_path) -> None:
    first = create_memory_runtime(str(tmp_path / "first"))
    second = create_memory_runtime(str(tmp_path / "second"))

    assert first is not second
    assert first.store is not second.store
    assert first.registry is not second.registry
    assert first.dream_scheduler is not second.dream_scheduler


def test_dream_scheduler_uses_runtime_state_root(tmp_path) -> None:
    state_root = str(tmp_path / "state")
    runtime = create_memory_runtime(state_root)
    assert runtime.dream_scheduler._state_root == state_root


def test_close_persists_registry_and_derived_indexes(tmp_path) -> None:
    state_root = str(tmp_path / "state")
    runtime = create_memory_runtime(state_root)
    entry = MemoryEntryInput(
        timestamp="2026-07-12T00:00:00+00:00",
        user_snippet="explicit runtime",
        summary="memory runtime owns persistence",
        facts=[],
    )
    runtime.keyword_index.index_entry("session", entry)

    runtime.close()

    assert os.path.isfile(os.path.join(state_root, "memory-registry.json"))
    assert os.path.isfile(os.path.join(state_root, "keyword-index.json"))


@pytest.mark.asyncio
async def test_shutdown_closes_all_async_memory_resources(tmp_path) -> None:
    runtime = create_memory_runtime(str(tmp_path / "state"))
    dream_shutdown = AsyncMock()
    embedding_close = AsyncMock()
    runtime.dream_scheduler.shutdown = dream_shutdown  # type: ignore[method-assign]
    runtime.embedding_provider.close = embedding_close  # type: ignore[method-assign]

    await runtime.shutdown()

    dream_shutdown.assert_awaited_once()
    embedding_close.assert_awaited_once()
