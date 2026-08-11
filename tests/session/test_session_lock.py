"""Tests for transactional session leases."""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

import miniagent.assistant.engine.session_lock as session_lock
from miniagent.assistant.engine.session_lock import (
    is_session_locked,
    release_session_lock,
    renew_session_leases,
    try_lock_session,
    try_lock_session_async,
)
from miniagent.assistant.infrastructure.process_utils import (
    is_process_running,
    is_process_running_async,
)
from miniagent.assistant.state.sync import open_state_database


@pytest.fixture
def mock_workspaces(tmp_path: Path):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    with patch(
        "miniagent.assistant.engine.session_lock._get_workspaces_dir",
        return_value=str(sessions),
    ):
        session_lock._HELD_RESOURCES.clear()
        try:
            yield sessions
        finally:
            session_lock._HELD_RESOURCES.clear()


def _put_foreign_lease(
    root: Path,
    session_id: str,
    pid: int | str = 999999,
    *,
    expires_at_ms: int | None = None,
) -> None:
    with open_state_database(root.parent) as connection:
        connection.execute(
            "INSERT INTO process_leases VALUES (?, ?, ?, 1)",
            (
                f"session:{session_id}",
                str(pid),
                expires_at_ms or int((time.time() + 60) * 1000),
            ),
        )


def test_try_lock_session_success_and_idempotence(mock_workspaces: Path) -> None:
    assert try_lock_session("sess-abc") == (True, "")
    assert try_lock_session("sess-abc") == (True, "")
    assert not (mock_workspaces / "sess-abc" / ".lock").exists()


def test_try_lock_session_conflict(mock_workspaces: Path) -> None:
    _put_foreign_lease(mock_workspaces, "sess-conflict")
    with patch(
        "miniagent.assistant.engine.session_lock.is_process_running", return_value=True
    ):
        ok, reason = try_lock_session("sess-conflict")
    assert not ok
    assert "999999" in reason


def test_dead_owner_is_replaced(mock_workspaces: Path) -> None:
    _put_foreign_lease(mock_workspaces, "sess-stale", pid=1)
    with patch(
        "miniagent.assistant.engine.session_lock.is_process_running", return_value=False
    ):
        assert try_lock_session("sess-stale") == (True, "")


def test_non_pid_owner_is_replaced(mock_workspaces: Path) -> None:
    _put_foreign_lease(mock_workspaces, "sess-invalid", pid="worker")
    assert try_lock_session("sess-invalid") == (True, "")


def test_safe_session_id_is_used_as_resource(mock_workspaces: Path) -> None:
    assert try_lock_session("feishu:oc_abc123") == (True, "")
    with open_state_database(mock_workspaces.parent) as connection:
        row = connection.execute(
            "SELECT owner FROM process_leases WHERE resource='session:feishu_oc_abc123'"
        ).fetchone()
    assert int(row[0]) == os.getpid()


@pytest.mark.asyncio
async def test_try_lock_session_async(mock_workspaces: Path) -> None:
    assert await try_lock_session_async("sess-async") == (True, "")


def test_release_owned_session_lock(mock_workspaces: Path) -> None:
    try_lock_session("sess-release")
    release_session_lock("sess-release")
    assert try_lock_session("sess-release") == (True, "")


def test_release_foreign_session_lock_is_noop(mock_workspaces: Path) -> None:
    _put_foreign_lease(mock_workspaces, "sess-other")
    release_session_lock("sess-other")
    with patch(
        "miniagent.assistant.engine.session_lock.is_process_running", return_value=True
    ):
        assert is_session_locked("sess-other") == 999999


def test_own_session_is_not_reported_as_foreign(mock_workspaces: Path) -> None:
    try_lock_session("sess-check")
    assert is_session_locked("sess-check") is None


def test_renew_session_leases_updates_every_held_resource(
    mock_workspaces: Path,
) -> None:
    assert try_lock_session("first") == (True, "")
    assert try_lock_session("second") == (True, "")
    assert renew_session_leases() == 2


def test_renew_session_leases_is_noop_without_held_resources(
    mock_workspaces: Path,
) -> None:
    assert renew_session_leases() == 0


def test_missing_expired_and_non_pid_owners_are_not_live(mock_workspaces: Path) -> None:
    assert is_session_locked("missing") is None
    _put_foreign_lease(
        mock_workspaces,
        "expired",
        expires_at_ms=int((time.time() - 1) * 1000),
    )
    assert is_session_locked("expired") is None
    _put_foreign_lease(mock_workspaces, "invalid", pid="worker")
    assert is_session_locked("invalid") is None


def test_is_process_running_current() -> None:
    assert is_process_running(os.getpid())


@pytest.mark.asyncio
async def test_is_process_running_async_current() -> None:
    assert await is_process_running_async(os.getpid()) is True


def test_is_process_running_fake() -> None:
    assert not is_process_running(999999999)
