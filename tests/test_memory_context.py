"""``memory_context`` Protocol 与默认实现测试。"""

from __future__ import annotations

import asyncio
import tempfile
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from miniagent.memory.history_bridge import format_history_for_llm
from miniagent.memory.keyword_index import format_search_results
from miniagent.memory.memory_context_service import (
    DefaultMemoryContext,
    DefaultMemoryHistory,
    DefaultMemorySearch,
    create_default_memory_context,
)
from miniagent.memory.store import DefaultMemoryStore
from miniagent.types.config import AgentConfig
from miniagent.types.memory_context import (
    MemoryContextProtocol,
    MemoryHistoryProtocol,
    MemoryInjectionResult,
    MemorySearchProtocol,
)


@pytest.fixture
def memory_bundle():
    from miniagent.memory.keyword_index import KeywordIndex

    with tempfile.TemporaryDirectory() as tmpdir:
        ki = KeywordIndex(state_dir=tmpdir)
        ms = DefaultMemoryStore(state_dir=tmpdir, keyword_index=ki)
        yield ms, ki


@pytest.mark.asyncio
async def test_default_memory_context_inject_metadata(memory_bundle) -> None:
    ms, ki = memory_bundle
    ctx = DefaultMemoryContext(ms, ki)
    from miniagent.types.config import SessionBindingConfig

    agent_config = AgentConfig(
        session_config=SessionBindingConfig(session_key="session-a")
    )

    _, metadata = await ctx.inject_memory_to_messages(
        [],
        "session-a",
        agent_config,
        user_input="hello",
    )

    assert "turn_keyword_context" in metadata
    assert metadata["relevant_count"] == 0


@pytest.mark.asyncio
async def test_default_memory_context_save_after_turn(memory_bundle) -> None:
    ms, ki = memory_bundle
    ctx = DefaultMemoryContext(ms, ki)
    real_save_unlocked = ms._save_unlocked
    ms._save_unlocked = AsyncMock(wraps=real_save_unlocked)  # type: ignore[method-assign]

    await ctx.save_memory_after_turn(
        "session-a",
        "用户问天气",
        "今天晴",
        ms,
        tool_calls=[],
    )

    memory = await ms.load("session-a")
    assert memory is not None
    assert memory.entries
    assert ms._save_unlocked.await_count == 1  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_save_after_turn_extracts_facts_from_tool_results(memory_bundle) -> None:
    ms, ki = memory_bundle
    ctx = DefaultMemoryContext(ms, ki)

    await ctx.save_memory_after_turn(
        "session-tool-fact",
        "读取设置",
        "读取完成",
        ms,
        tool_calls=[{
            "name": "read_file",
            "result": "记住：以后默认使用 Markdown",
        }],
    )

    memory = await ms.load("session-tool-fact")
    assert memory is not None
    assert any("Markdown" in fact for fact in memory.key_facts)


@pytest.mark.asyncio
async def test_save_after_turn_flush_does_not_block_event_loop(memory_bundle) -> None:
    ms, ki = memory_bundle
    ctx = DefaultMemoryContext(ms, ki)
    tick_time: float | None = None

    def slow_save() -> None:
        time.sleep(0.1)

    async def heartbeat() -> None:
        nonlocal tick_time
        await asyncio.sleep(0.02)
        tick_time = time.perf_counter()

    ki.save = slow_save  # type: ignore[method-assign]
    heartbeat_task = asyncio.create_task(heartbeat())
    await ctx.save_memory_after_turn("responsive", "hello", "world", ms)
    save_returned_at = time.perf_counter()
    await heartbeat_task

    assert tick_time is not None
    assert tick_time < save_returned_at


@pytest.mark.asyncio
async def test_save_after_turn_keeps_legacy_store_compatibility() -> None:
    store = MagicMock()
    store.record_turn = None
    store.flush_keyword_index_async = None
    store.flush_keyword_index = None
    store.update_summary = AsyncMock()
    store.add_entry = AsyncMock()
    ctx = DefaultMemoryContext(store, MagicMock())

    await ctx.save_memory_after_turn("legacy", "hello", "world", store)

    store.update_summary.assert_awaited_once()
    store.add_entry.assert_awaited_once()


def test_memory_injection_result_from_tuple() -> None:
    result = MemoryInjectionResult.from_tuple(([], {"relevant_count": 0}))
    assert result.messages == []
    assert result.memory_metadata["relevant_count"] == 0


def test_format_search_results_max_length() -> None:
    results = [{"session_id": "s1", "summary": "x" * 200}]
    text = format_search_results(results, max_length=40)
    assert len(text) <= 40
    assert "截断" in text


def test_format_history_for_llm_max_tokens() -> None:
    history = [
        {"role": "user", "content": "short"},
        {"role": "assistant", "content": "also short"},
        {"role": "user", "content": "latest"},
    ]
    out = format_history_for_llm(history, max_tokens=30)
    assert out
    assert out[-1]["content"] == "latest"


@pytest.mark.asyncio
async def test_default_memory_history_load_with_manager() -> None:
    manager = MagicMock()
    manager.load_session_history_async = AsyncMock(
        return_value=[{"role": "user", "content": "hi"}]
    )
    history = DefaultMemoryHistory(manager)
    rows = await history.load_history("session-1", max_messages=1)
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_default_memory_history_sync_loader_does_not_block_loop() -> None:
    manager = MagicMock()

    def slow_load(_session_key: str):
        time.sleep(0.1)
        return [{"role": "user", "content": "hi"}]

    manager.load_session_history_async = None
    manager.load_session_history = slow_load
    history = DefaultMemoryHistory(manager)
    heartbeat_time: float | None = None

    async def heartbeat() -> None:
        nonlocal heartbeat_time
        await asyncio.sleep(0.02)
        heartbeat_time = time.perf_counter()

    heartbeat_task = asyncio.create_task(heartbeat())
    rows = await history.load_history("session-1")
    load_returned_at = time.perf_counter()
    await heartbeat_task

    assert heartbeat_time is not None
    assert heartbeat_time < load_returned_at
    assert rows == [{"role": "user", "content": "hi"}]


@pytest.mark.asyncio
async def test_default_memory_search_protocol(memory_bundle) -> None:
    ms, ki = memory_bundle
    search: MemorySearchProtocol = DefaultMemorySearch(ki, ms)
    results = await search.search_relevant_memory("test", "session-a", top_k=3)
    assert isinstance(results, list)
    formatted = search.format_search_results(results, max_length=100)
    assert isinstance(formatted, str)


def test_create_default_memory_context_is_protocol(memory_bundle) -> None:
    ms, ki = memory_bundle
    ctx = create_default_memory_context(ms, ki)
    assert isinstance(ctx, MemoryContextProtocol)
    assert isinstance(ctx, DefaultMemoryContext)


def test_default_memory_history_is_protocol() -> None:
    assert isinstance(DefaultMemoryHistory(), MemoryHistoryProtocol)
