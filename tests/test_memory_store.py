"""Tests for memory store (async)."""

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from miniagent.infrastructure.tracing import clear_trace_hooks, register_trace_hook
from miniagent.memory.store import DefaultMemoryStore
from miniagent.types.memory import (
    FileMetadata,
    GroundTruthFact,
    MemoryEntry,
    MemoryEntryInput,
    SessionMemory,
)


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
                    "key_facts": ["saved fact"],
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
        assert loaded.key_facts == ["saved fact"]
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

    async def test_cache_lru_ttl_cleanup_and_lock_reuse(self):
        first = SessionMemory(session_id="first")
        second = SessionMemory(session_id="second")
        self.store._cache_max = 1
        self.store._cache_cleanup_interval = 0
        self.store._cache_ttl_seconds = 10
        self.store._cache_put("first", first)
        self.store._cache_put("second", second)
        assert self.store._cache_get("first") == (None, None)
        assert self.store._cache_get("second")[0] is second

        self.store._cache["expired"] = (first, 0)
        assert self.store._cache_get("expired", now=20) == (None, None)
        self.store._cache["expired"] = (first, 0)
        self.store._cleanup_expired_cache(20)
        assert "expired" not in self.store._cache

        lock = await self.store._get_session_lock("same")
        assert await self.store._get_session_lock("same") is lock
        self.store._session_locks_max = 1
        await self.store._get_session_lock("replacement")
        assert "replacement" in self.store._session_locks
        self.store.evict_session("replacement")
        assert "replacement" not in self.store._session_locks

    async def test_memory_from_dict_skips_invalid_nested_records(self):
        entry = MemoryEntry(timestamp="t", user_snippet="u", summary="s")
        fact = GroundTruthFact(key="k", value="v")
        memory = self.store._memory_from_dict(
            {
                "session_id": "nested",
                "entries": [entry, {"facts": object()}, "ignored"],
                "uploaded_files": [
                    {
                        "name": "ok.txt",
                        "path": "p",
                        "size": 1,
                        "type": "text",
                    },
                    {"name": "bad", "size": "not-an-int"},
                ],
                "ground_truth_facts": [
                    fact,
                    {
                        "key": "next",
                        "value": "value",
                        "confidence": 0.5,
                        "supersedes": "k",
                    },
                    {"key": "bad", "confidence": "not-a-float"},
                ],
            }
        )
        assert memory.entries == [entry]
        assert [item.name for item in memory.uploaded_files] == ["ok.txt"]
        assert [item.key for item in memory.ground_truth_facts] == ["k", "next"]
        assert memory.ground_truth_facts[1].supersedes == "k"

    async def test_append_message_all_roles_and_empty_content(self):
        await self.store.append_message("messages", "user", "first question")
        await self.store.append_message("messages", "user", "revised question")
        await self.store.append_message("messages", "assistant", "answer")
        await self.store.append_message("messages", "assistant", "standalone answer")
        await self.store.append_message("messages", "tool", "tool output")
        await self.store.append_message("messages", "tool", "second output")
        await self.store.append_message("messages", "user", "")

        memory = await self.store.load("messages")
        assert memory is not None
        assert memory.entries[0].user_snippet == "revised question"
        assert memory.entries[0].summary == "answer"
        assert memory.entries[1].summary == "standalone answer"
        assert "tool output" in memory.cumulative_summary
        assert "second output" in memory.cumulative_summary

    async def test_record_turn_merges_existing_state_and_indexes(self, monkeypatch):
        keyword_index = MagicMock()
        provider = MagicMock()
        provider.queue_index = AsyncMock()
        store = DefaultMemoryStore(
            state_dir=self.tmpdir.name,
            keyword_index=keyword_index,
            embedding_provider=provider,
        )
        await store.update_user_snippet("turn", "in progress")
        monkeypatch.setattr(
            "miniagent.memory.embedding_search.embedding_search_enabled", lambda: True
        )

        await store.record_turn(
            "turn",
            "summary",
            ["fact", "FACT"],
            {
                "timestamp": "",
                "user_snippet": "",
                "summary": "completed",
                "facts": None,
            },
        )
        await store.record_turn(
            "turn",
            "second",
            [f"fact-{i}" for i in range(25)],
            MemoryEntryInput(
                timestamp="t2",
                user_snippet="next",
                summary="done",
                facts=["entry-fact"],
            ),
        )

        memory = await store.load("turn")
        assert memory is not None
        assert memory.total_turns == 2
        assert memory.entries[0].user_snippet == "in progress"
        assert len(memory.key_facts) == 20
        assert keyword_index.index_entry.call_count == 2
        assert provider.queue_index.await_count == 2

    async def test_embedding_index_compatibility_and_failures(self, monkeypatch):
        monkeypatch.setattr(
            "miniagent.memory.embedding_search.embedding_search_enabled", lambda: True
        )
        provider = MagicMock(spec=[])
        provider.get_embedding = AsyncMock(return_value=[0.1])
        provider.index = MagicMock()
        store = DefaultMemoryStore(
            state_dir=self.tmpdir.name,
            keyword_index=MagicMock(),
            embedding_provider=provider,
        )
        entry = MemoryEntryInput("t", "user", "summary", [])
        await store.add_entry("compat", entry)
        provider.index.index_entry.assert_called_once()

        provider.get_embedding.return_value = None
        store._keyword_index.index_entry.side_effect = RuntimeError("index failed")
        await store.add_entry("compat-none", entry)
        assert provider.index.index_entry.call_count == 1

        provider.get_embedding.side_effect = RuntimeError("embedding failed")
        await store.add_entry("compat-error", entry)

    async def test_add_file_limits_history_and_adds_description_fact(self):
        for index in range(52):
            await self.store.add_file(
                "files",
                FileMetadata(
                    name=f"file-{index}.txt",
                    path=f"p-{index}",
                    size=index,
                    mime_type="text/plain",
                    type="text" if index == 51 else "binary",
                    description="described" if index == 51 else "",
                ),
            )

        memory = await self.store.load("files")
        assert memory is not None
        assert len(memory.uploaded_files) == 50
        assert memory.uploaded_files[0].name == "file-2.txt"
        assert any("file-51.txt" in fact for fact in memory.key_facts)

        store = MagicMock()
        store.add_file = AsyncMock()
        from miniagent.memory.store import add_file_to_memory

        meta = FileMetadata(
            name="one", path="p", size=1, mime_type="text/plain", type="text"
        )
        await add_file_to_memory("session", meta, store)
        store.add_file.assert_awaited_once_with("session", meta)

    async def test_flush_keyword_index_handles_success_and_failure(self):
        index = MagicMock()
        store = DefaultMemoryStore(state_dir=self.tmpdir.name, keyword_index=index)
        store.flush_keyword_index()
        index.save.assert_called_once()
        index.save.side_effect = RuntimeError("save failed")
        store.flush_keyword_index()
        await store.flush_keyword_index_async()
