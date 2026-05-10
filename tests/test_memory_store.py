"""Tests for memory store (async)."""

import tempfile
import pytest
from miniagent.memory.store import DefaultMemoryStore
from miniagent.types.memory import MemoryEntryInput


@pytest.mark.asyncio
class TestMemoryStore:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.store = DefaultMemoryStore(state_dir=self.tmpdir.name)
        yield
        self.tmpdir.cleanup()

    async def test_load_nonexistent(self):
        result = await self.store.load("test-session")
        assert result is None

    async def test_add_entry(self):
        sid = "session-add"
        # Create initial memory by calling save
        from miniagent.types.memory import SessionMemory
        from datetime import datetime, timezone
        memory = SessionMemory(
            session_id=sid,
            cumulative_summary="",
            key_facts=[],
            entries=[],
            total_turns=0,
            first_seen=datetime.now(timezone.utc).isoformat(),
            last_active=datetime.now(timezone.utc).isoformat(),
        )
        await self.store.save(memory)

        entry = MemoryEntryInput(
            timestamp=datetime.now(timezone.utc).isoformat(),
            user_snippet="Hello",
            summary="User said hello",
            facts=[],
        )
        await self.store.add_entry(sid, entry)
        loaded = await self.store.load(sid)
        assert loaded is not None
        assert len(loaded.entries) == 1

    async def test_add_entry_accepts_plain_dict(self):
        """executor 等调用方曾传入 dict；须与 MemoryEntryInput 等价处理。"""
        sid = "session-dict-entry"
        from miniagent.types.memory import SessionMemory
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        memory = SessionMemory(
            session_id=sid,
            cumulative_summary="",
            key_facts=[],
            entries=[],
            total_turns=0,
            first_seen=now,
            last_active=now,
        )
        await self.store.save(memory)

        await self.store.add_entry(
            sid,
            {
                "timestamp": now,
                "user_snippet": "ping",
                "summary": "pong",
                "facts": ["a"],
            },
        )
        loaded = await self.store.load(sid)
        assert loaded is not None
        assert len(loaded.entries) == 1
        assert loaded.entries[0].user_snippet == "ping"
        assert loaded.entries[0].facts == ["a"]

    async def test_update_summary(self):
        sid = "session-update"
        from miniagent.types.memory import SessionMemory
        from datetime import datetime, timezone
        memory = SessionMemory(
            session_id=sid,
            cumulative_summary="",
            key_facts=[],
            entries=[],
            total_turns=0,
            first_seen=datetime.now(timezone.utc).isoformat(),
            last_active=datetime.now(timezone.utc).isoformat(),
        )
        await self.store.save(memory)
        await self.store.update_summary(sid, "User likes Python", ["Python fan"])
        loaded = await self.store.load(sid)
        assert loaded is not None
        assert "Python" in loaded.cumulative_summary
        assert any("Python" in f for f in loaded.key_facts)

    async def test_search_empty(self):
        # Memory store doesn't have a search method by default
        # Verify that load returns None for nonexistent session
        result = await self.store.load("nonexistent")
        assert result is None
