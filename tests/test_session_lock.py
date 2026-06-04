"""Tests for miniagent.engine.session_lock."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from miniagent.engine.session_lock import (
    is_session_locked,
    release_session_lock,
    try_lock_session,
)
from miniagent.infrastructure.process_utils import is_process_running


@pytest.fixture
def mock_workspaces(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect workspaces dir to tmp_path by patching where it's imported."""
    with patch("miniagent.engine.session_lock._get_workspaces_dir", return_value=str(tmp_path)):
        # Also need to patch the session.manager module for consistency
        with patch("miniagent.session.manager._get_workspaces_dir", return_value=str(tmp_path)):
            yield tmp_path


def test_try_lock_session_success(mock_workspaces: Path) -> None:
    ok, reason = try_lock_session("sess-abc")
    assert ok
    assert reason == ""
    assert (mock_workspaces / "sess-abc" / ".lock").exists()


def test_try_lock_idempotent(mock_workspaces: Path) -> None:
    try_lock_session("sess-x")
    ok, reason = try_lock_session("sess-x")
    assert ok
    assert reason == ""


def test_try_lock_session_conflict(mock_workspaces: Path) -> None:
    lock_dir = mock_workspaces / "sess-conflict"
    lock_dir.mkdir()
    lock_file = lock_dir / ".lock"
    lock_file.write_text("999999", encoding="utf-8")

    with patch("miniagent.engine.session_lock.is_process_running", return_value=True):
        ok, reason = try_lock_session("sess-conflict")
    assert not ok
    assert "999999" in reason


def test_try_lock_stale_pid(mock_workspaces: Path) -> None:
    lock_dir = mock_workspaces / "sess-stale"
    lock_dir.mkdir()
    lock_file = lock_dir / ".lock"
    lock_file.write_text("1", encoding="utf-8")

    with patch("miniagent.engine.session_lock.is_process_running", return_value=False):
        ok, reason = try_lock_session("sess-stale")
    assert ok
    assert lock_file.read_text(encoding="utf-8") == str(os.getpid())


def test_release_session_lock(mock_workspaces: Path) -> None:
    try_lock_session("sess-release")
    assert (mock_workspaces / "sess-release" / ".lock").exists()
    release_session_lock("sess-release")
    assert not (mock_workspaces / "sess-release" / ".lock").exists()


def test_release_non_owned_lock_noop(mock_workspaces: Path) -> None:
    lock_dir = mock_workspaces / "sess-other"
    lock_dir.mkdir()
    lock_file = lock_dir / ".lock"
    lock_file.write_text("999999", encoding="utf-8")
    release_session_lock("sess-other")
    assert lock_file.exists()


def test_is_session_locked(mock_workspaces: Path) -> None:
    try_lock_session("sess-check")
    pid = is_session_locked("sess-check")
    assert pid is None  # own lock


def test_is_session_locked_by_other(mock_workspaces: Path) -> None:
    lock_dir = mock_workspaces / "sess-other2"
    lock_dir.mkdir()
    lock_file = lock_dir / ".lock"
    lock_file.write_text("999999", encoding="utf-8")

    with patch("miniagent.engine.session_lock.is_process_running", return_value=True):
        pid = is_session_locked("sess-other2")
    assert pid == 999999


def test_is_process_running_current() -> None:
    assert is_process_running(os.getpid())


def test_is_process_running_fake() -> None:
    assert not is_process_running(999999999)
