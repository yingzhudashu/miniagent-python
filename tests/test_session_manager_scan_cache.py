"""SessionManager disk discovery cache and lock lifecycle tests."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from miniagent.infrastructure.registry import DefaultToolRegistry
from miniagent.session.manager import DefaultSessionManager


@pytest.fixture
def workspaces(tmp_path: Path) -> Path:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    with patch("miniagent.session.manager._get_workspaces_dir", return_value=str(sessions)):
        yield sessions


def _write_config(workspaces: Path, index: int, *, title: str = "") -> Path:
    session_id = f"session-{index}"
    workspace = workspaces / session_id
    workspace.mkdir(exist_ok=True)
    config = workspace / "config.json"
    config.write_text(
        json.dumps(
            {
                "session_id": session_id,
                "workspace_path": str(workspace),
                "files_path": str(workspace / "files"),
                "skills_path": str(workspace / "skills"),
                "created_at": "2026-01-01T00:00:00+00:00",
                "last_active": "2026-01-01T00:00:00+00:00",
                "session_number": index + 1,
                "title": title,
            }
        ),
        encoding="utf-8",
    )
    return config


def test_repeated_session_listing_reuses_unchanged_parsed_configs(workspaces: Path) -> None:
    for index in range(12):
        _write_config(workspaces, index)
    manager = DefaultSessionManager(DefaultToolRegistry())

    with patch("miniagent.session.manager.json.load", wraps=json.load) as json_load:
        sessions = manager.list_all_sessions_with_info()

    assert len(sessions) == 12
    json_load.assert_not_called()


def test_session_config_cache_refreshes_after_external_replace(workspaces: Path) -> None:
    config = _write_config(workspaces, 0, title="before")
    manager = DefaultSessionManager(DefaultToolRegistry())
    old_stat = config.stat()

    replacement = config.with_suffix(".replacement")
    replacement.write_text(
        config.read_text(encoding="utf-8").replace("before", "after!"), encoding="utf-8"
    )
    os.replace(replacement, config)
    os.utime(config, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns + 1_000_000))

    sessions = manager.list_all_sessions_with_info()

    assert sessions[0]["title"] == "after!"


def test_session_config_cache_has_hard_entry_limit(workspaces: Path) -> None:
    for index in range(8):
        _write_config(workspaces, index)
    manager = DefaultSessionManager(DefaultToolRegistry())
    manager._disk_config_cache.clear()
    manager._disk_config_cache_max = 3

    assert len(manager.list_all_sessions_with_info()) == 8
    assert len(manager._disk_config_cache) <= 3


def test_lru_eviction_and_destroy_release_idle_session_locks(workspaces: Path) -> None:
    manager = DefaultSessionManager(DefaultToolRegistry(), max_sessions=3)
    for index in range(20):
        manager.get_or_create(f"ephemeral-{index}")

    assert len(manager._sessions) == 3
    assert set(manager._session_locks) == set(manager._sessions)

    for session_id in list(manager._sessions):
        assert manager.destroy(session_id, keep_files=False)

    assert manager._session_locks == {}
    assert manager._session_lock_users == {}
