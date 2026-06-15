"""Protocol 合规性测试：记忆与会话管理契约。"""

from __future__ import annotations

import tempfile

import pytest

from miniagent.infrastructure.registry import DefaultToolRegistry
from miniagent.memory.store import DefaultMemoryStore
from miniagent.session.manager import DefaultSessionManager
from miniagent.types.memory import MemoryStoreProtocol, SessionManagerProtocol


class TestMemoryStoreProtocolCompliance:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.store = DefaultMemoryStore(state_dir=self.tmpdir.name)
        yield
        self.tmpdir.cleanup()

    def test_isinstance_memory_store_protocol(self) -> None:
        assert isinstance(self.store, MemoryStoreProtocol)

    def test_has_state_dir(self) -> None:
        assert getattr(self.store, "_state_dir", None) == self.tmpdir.name

    def test_update_user_snippet_not_protocol_stub(self) -> None:
        """实现类方法须来自 store 模块，而非 Protocol 空 stub。"""
        assert DefaultMemoryStore.update_user_snippet.__qualname__ == "DefaultMemoryStore.update_user_snippet"

    @pytest.mark.asyncio
    async def test_update_user_snippet_persists(self) -> None:
        sid = "snippet-session"
        await self.store.update_user_snippet(sid, "hello world")
        loaded = await self.store.load(sid)
        assert loaded is not None
        assert len(loaded.entries) == 1
        assert loaded.entries[0].user_snippet == "hello world"

    @pytest.mark.asyncio
    async def test_append_message_user_and_assistant(self) -> None:
        sid = "append-session"
        await self.store.append_message(sid, "user", "question?")
        await self.store.append_message(sid, "assistant", "answer.")
        loaded = await self.store.load(sid)
        assert loaded is not None
        assert len(loaded.entries) == 1
        assert loaded.entries[0].user_snippet == "question?"
        assert loaded.entries[0].summary == "answer."

    @pytest.mark.asyncio
    async def test_add_entry_merges_in_progress_entry(self) -> None:
        from datetime import datetime, timezone

        from miniagent.types.memory import MemoryEntryInput

        sid = "merge-session"
        now = datetime.now(timezone.utc).isoformat()
        await self.store.update_user_snippet(sid, "early snippet")
        await self.store.add_entry(
            sid,
            MemoryEntryInput(
                timestamp=now,
                user_snippet="final snippet",
                summary="done",
                facts=["fact-a"],
            ),
        )
        loaded = await self.store.load(sid)
        assert loaded is not None
        assert len(loaded.entries) == 1
        assert loaded.entries[0].user_snippet == "final snippet"
        assert loaded.entries[0].summary == "done"
        assert loaded.entries[0].facts == ["fact-a"]
        assert loaded.total_turns == 1


class TestSessionManagerProtocolCompliance:
    @pytest.fixture(autouse=True)
    def setup(self, monkeypatch: pytest.MonkeyPatch):
        self.tmpdir = tempfile.TemporaryDirectory()
        monkeypatch.setenv("MINIAGENT_PATHS_STATE_DIR", self.tmpdir.name)
        self.manager = DefaultSessionManager(DefaultToolRegistry())
        yield
        self.tmpdir.cleanup()

    def test_isinstance_session_manager_protocol(self) -> None:
        assert isinstance(self.manager, SessionManagerProtocol)

    def test_protocol_methods_not_stubs(self) -> None:
        assert DefaultSessionManager.get_or_create.__qualname__ == "DefaultSessionManager.get_or_create"
        assert DefaultSessionManager.promote_tool.__qualname__ == "DefaultSessionManager.promote_tool"
