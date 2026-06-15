"""Tests for memory store (async)."""

import json
import tempfile
from pathlib import Path

import pytest

from miniagent.infrastructure.tracing import clear_trace_hooks, register_trace_hook
from miniagent.memory.store import DefaultMemoryStore
from miniagent.types.memory import GroundTruthFact, MemoryEntryInput


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
        from datetime import datetime, timezone

        from miniagent.types.memory import SessionMemory

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
        from datetime import datetime, timezone

        from miniagent.types.memory import SessionMemory

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
        from datetime import datetime, timezone

        from miniagent.types.memory import SessionMemory

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

    async def test_add_entry_uses_locked_load_without_public_trace(self):
        """add_entry 已持有 session lock 时不应再走 public load 的 trace 路径。"""
        sid = "session-locked-load"
        from datetime import datetime, timezone

        from miniagent.types.memory import SessionMemory

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

        events: list[dict] = []
        clear_trace_hooks()
        register_trace_hook(events.append)

        try:
            await self.store.add_entry(
                sid,
                MemoryEntryInput(
                    timestamp=now,
                    user_snippet="Hello",
                    summary="User said hello",
                    facts=[],
                ),
            )
        finally:
            clear_trace_hooks()

        assert [e.get("operation") for e in events].count("memory.read") == 0

    async def test_save_uses_compact_json_and_loads_back(self):
        sid = "session-compact-json"
        from datetime import datetime, timezone

        from miniagent.types.memory import SessionMemory

        now = datetime.now(timezone.utc).isoformat()
        memory = SessionMemory(
            session_id=sid,
            cumulative_summary="summary",
            key_facts=["fact"],
            entries=[],
            total_turns=0,
            first_seen=now,
            last_active=now,
        )
        await self.store.save(memory)

        text = Path(self.tmpdir.name, "memory", f"{sid}.json").read_text(encoding="utf-8")
        assert "\n  " not in text
        loaded = await self.store.load(sid)
        assert loaded is not None
        assert loaded.session_id == sid
        assert loaded.key_facts == ["fact"]

    async def test_load_old_schema_without_ground_truth(self):
        sid = "session-old-schema"
        memory_dir = Path(self.tmpdir.name, "memory")
        memory_dir.mkdir(parents=True)
        Path(memory_dir, f"{sid}.json").write_text(
            json.dumps(
                {
                    "session_id": sid,
                    "cumulative_summary": "",
                    "key_facts": ["legacy fact"],
                    "entries": [],
                    "uploaded_files": [],
                    "total_turns": 0,
                    "first_seen": "",
                    "last_active": "",
                    "chat_id": None,
                    "sender_id": None,
                }
            ),
            encoding="utf-8",
        )

        loaded = await self.store.load(sid)

        assert loaded is not None
        assert loaded.key_facts == ["legacy fact"]
        assert loaded.ground_truth_facts == []

    async def test_update_user_snippet_truncates_and_updates_in_progress(self):
        sid = "session-snippet"
        long_text = "x" * 150
        await self.store.update_user_snippet(sid, long_text)
        loaded = await self.store.load(sid)
        assert loaded is not None
        assert len(loaded.entries[0].user_snippet) == 100

        await self.store.update_user_snippet(sid, "revised")
        loaded = await self.store.load(sid)
        assert loaded is not None
        assert len(loaded.entries) == 1
        assert loaded.entries[0].user_snippet == "revised"

    async def test_append_message_system_role_updates_summary(self):
        sid = "session-system"
        await self.store.append_message(sid, "system", "boot note")
        loaded = await self.store.load(sid)
        assert loaded is not None
        assert "boot note" in loaded.cumulative_summary
        assert loaded.entries == []

    async def test_save_and_load_ground_truth_facts(self):
        sid = "session-ground-truth"
        from miniagent.types.memory import SessionMemory

        memory = SessionMemory(
            session_id=sid,
            ground_truth_facts=[
                GroundTruthFact(
                    key="output.language",
                    value="默认用中文",
                    category="output_format",
                    evidence="记住以后回复都用中文",
                )
            ],
        )

        await self.store.save(memory)
        loaded = await self.store.load(sid)

        assert loaded is not None
        assert loaded.ground_truth_facts[0].key == "output.language"
        assert loaded.ground_truth_facts[0].value == "默认用中文"

    async def test_update_summary_supersedes_ground_truth_fact(self):
        sid = "session-ground-truth-update"

        await self.store.update_summary(sid, "记住以后回复都用中文", ["以后回复都用中文"])
        await self.store.update_summary(sid, "纠正一下，以后回复都用英文", ["以后回复都用英文"])

        loaded = await self.store.load(sid)
        assert loaded is not None
        active = [f for f in loaded.ground_truth_facts if f.status == "active"]
        assert len(active) == 1
        assert "英文" in active[0].value
        assert any(f.status == "superseded" for f in loaded.ground_truth_facts)
