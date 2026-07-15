"""Tests for miniagent/memory/shared_registry.py."""

import os
import tempfile

import pytest

from miniagent.agent.types.memory import MemoryEntryInput
from miniagent.assistant.memory.shared_registry import (
    MemoryEntryRegistry,
    SharedEntry,
)
from tests.config_helpers import install_test_config


@pytest.fixture(autouse=True)
def _isolated_registry_config(tmp_path):
    """Reset JsonConfigLoader so registry_max_entries tests don't leak."""
    install_test_config(tmp_path, {})


@pytest.fixture
def temp_state_dir():
    """Create a temporary state directory for each test."""
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def registry(temp_state_dir):
    """Create a fresh registry for each test."""
    r = MemoryEntryRegistry(state_dir=temp_state_dir)
    yield r
    r.clear()


class TestSharedEntry:
    """Tests for SharedEntry dataclass."""

    def test_create_basic(self):
        """Create entry with required fields."""
        entry = SharedEntry(
            session_id="test-session",
            timestamp="2026-05-31T12:00:00+08:00",
            user_snippet="User input",
            summary="Summary text",
        )
        assert entry.session_id == "test-session"
        assert entry.timestamp == "2026-05-31T12:00:00+08:00"
        assert entry.user_snippet == "User input"
        assert entry.summary == "Summary text"
        assert entry.facts == []

    def test_create_with_facts(self):
        """Create entry with facts list."""
        entry = SharedEntry(
            session_id="s1",
            timestamp="2026-05-31T12:00:00Z",
            user_snippet="u",
            summary="s",
            facts=["fact1", "fact2"],
        )
        assert entry.facts == ["fact1", "fact2"]


class TestMemoryEntryRegistry:
    """Tests for MemoryEntryRegistry."""

    def test_register_new_entry(self, registry):
        """Register a new memory entry."""
        entry = MemoryEntryInput(
            timestamp="2026-05-31T12:00:00+08:00",
            user_snippet="User query",
            summary="Conversation summary",
            facts=["key fact"],
        )
        key = registry.register("session-1", entry)
        assert key == "session-1:2026-05-31T12:00:00+08:00"

        retrieved = registry.get(key)
        assert retrieved is not None
        assert retrieved.session_id == "session-1"
        assert retrieved.user_snippet == "User query"
        assert retrieved.summary == "Conversation summary"
        assert retrieved.facts == ["key fact"]

    def test_register_duplicate_key_updates(self, registry):
        """Registering same key with changed content updates entry."""
        entry1 = MemoryEntryInput(
            timestamp="2026-05-31T12:00:00+08:00",
            user_snippet="Original",
            summary="Original summary",
            facts=["fact1"],
        )
        key = registry.register("session-1", entry1)

        entry2 = MemoryEntryInput(
            timestamp="2026-05-31T12:00:00+08:00",
            user_snippet="Updated",
            summary="Updated summary",
            facts=["fact2"],
        )
        registry.register("session-1", entry2)

        retrieved = registry.get(key)
        assert retrieved.user_snippet == "Updated"
        assert retrieved.facts == ["fact2"]

    def test_register_duplicate_no_change(self, registry):
        """Registering same key with unchanged content keeps existing."""
        entry = MemoryEntryInput(
            timestamp="2026-05-31T12:00:00+08:00",
            user_snippet="Same",
            summary="Same summary",
            facts=["fact"],
        )
        registry.register("session-1", entry)
        registry.register("session-1", entry)

        assert registry.get_stats()["total_entries"] == 1

    def test_eviction_on_max_entries(self, temp_state_dir, tmp_path):
        """Registry evicts oldest entries when exceeding max."""
        from tests.config_helpers import install_test_config

        install_test_config(tmp_path, {"memory": {"registry_max_entries": 3}})
        registry = MemoryEntryRegistry(state_dir=temp_state_dir)

        for i in range(5):
            entry = MemoryEntryInput(
                timestamp=f"2026-05-31T{i:02d}:00:00Z",
                user_snippet=f"Entry {i}",
                summary=f"Summary {i}",
                facts=[],
            )
            registry.register("session-1", entry)

        stats = registry.get_stats()
        assert stats["total_entries"] == 3

        # First two entries should be evicted
        assert registry.get("session-1:2026-05-31T00:00:00Z") is None
        assert registry.get("session-1:2026-05-31T01:00:00Z") is None
        assert registry.get("session-1:2026-05-31T02:00:00Z") is not None

    def test_contains(self, registry):
        """Check if key exists in registry."""
        entry = MemoryEntryInput(
            timestamp="2026-05-31T12:00:00Z",
            user_snippet="test",
            summary="test",
            facts=[],
        )
        key = registry.register("s1", entry)
        assert registry.contains(key)
        assert not registry.contains("nonexistent:key")

    def test_evict(self, registry):
        """Evict specific key from registry."""
        entry = MemoryEntryInput(
            timestamp="2026-05-31T12:00:00Z",
            user_snippet="test",
            summary="test",
            facts=[],
        )
        key = registry.register("s1", entry)
        assert registry.evict(key) is True
        assert registry.get(key) is None
        assert registry.evict(key) is False  # Already evicted

    def test_clear(self, registry):
        """Clear all entries."""
        for i in range(3):
            entry = MemoryEntryInput(
                timestamp=f"2026-05-31T{i:02d}:00:00Z",
                user_snippet=f"e{i}",
                summary=f"s{i}",
                facts=[],
            )
            registry.register("s1", entry)

        registry.clear()
        assert registry.get_stats()["total_entries"] == 0

    def test_save_and_load(self, registry, temp_state_dir):
        """Save registry to disk and reload."""
        entry = MemoryEntryInput(
            timestamp="2026-05-31T12:00:00Z",
            user_snippet="Persist me",
            summary="Persistent summary",
            facts=["persistent fact"],
        )
        registry.register("s1", entry)
        registry.save()

        # Create new registry to test load
        registry2 = MemoryEntryRegistry(state_dir=temp_state_dir)
        retrieved = registry2.get("s1:2026-05-31T12:00:00Z")
        assert retrieved is not None
        assert retrieved.user_snippet == "Persist me"

    def test_save_skip_if_not_dirty(self, registry, temp_state_dir):
        """Save does not write if registry not dirty."""
        registry.save()  # No entries, not dirty
        registry_file = os.path.join(temp_state_dir, "memory-registry.json")
        assert not os.path.exists(registry_file)

    def test_get_stats(self, registry):
        """Get registry statistics."""
        for i in range(5):
            entry = MemoryEntryInput(
                timestamp=f"2026-05-31T{i:02d}:00:00Z",
                user_snippet=f"e{i}",
                summary=f"s{i}",
                facts=[],
            )
            registry.register("s1", entry)

        stats = registry.get_stats()
        assert stats["total_entries"] == 5
