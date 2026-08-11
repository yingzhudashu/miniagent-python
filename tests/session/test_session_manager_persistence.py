"""SessionManager SQLite discovery and lock lifecycle tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from miniagent.agent.tools.registry import DefaultToolRegistry
from miniagent.assistant.session.manager import DefaultSessionManager


@pytest.fixture
def workspaces(tmp_path: Path) -> Path:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    with patch(
        "miniagent.assistant.session.manager._get_workspaces_dir",
        return_value=str(sessions),
    ):
        yield sessions


def test_session_listing_and_rename_survive_restart(workspaces: Path) -> None:
    first = DefaultSessionManager(DefaultToolRegistry())
    first.get_or_create("session-1")
    assert first.rename_session("session-1", "after")

    second = DefaultSessionManager(DefaultToolRegistry())
    sessions = second.list_all_sessions_with_info()
    assert len(sessions) == 1
    assert sessions[0]["title"] == "after"
    assert second.resolve_session_id("1") == "session-1"


@pytest.mark.asyncio
async def test_lru_eviction_and_destroy_release_idle_session_locks(workspaces: Path) -> None:
    manager = DefaultSessionManager(DefaultToolRegistry(), max_sessions=3)
    for index in range(20):
        manager.get_or_create(f"ephemeral-{index}")

    assert len(manager._sessions) == 3
    assert set(manager._session_locks) == set(manager._sessions)

    for session_id in list(manager._sessions):
        assert await manager.delete_session(session_id, keep_files=False)

    assert manager._session_locks == {}
    assert manager._session_lock_users == {}
