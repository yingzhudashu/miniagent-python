"""Tests for miniagent.assistant.engine.session_lock."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from miniagent.assistant.engine.session_lock import (
    is_session_locked,
    release_session_lock,
    try_lock_session,
    try_lock_session_async,
)
from miniagent.assistant.infrastructure.process_utils import (
    is_process_running,
    is_process_running_async,
)


@pytest.fixture
def mock_workspaces(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect workspaces dir to tmp_path by patching where it's imported."""
    with patch("miniagent.assistant.engine.session_lock._get_workspaces_dir", return_value=str(tmp_path)):
        # Also need to patch the session.manager module for consistency
        with patch("miniagent.assistant.session.manager._get_workspaces_dir", return_value=str(tmp_path)):
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

    with patch("miniagent.assistant.engine.session_lock.is_process_running", return_value=True):
        ok, reason = try_lock_session("sess-conflict")
    assert not ok
    assert "999999" in reason


def test_try_lock_stale_pid(mock_workspaces: Path) -> None:
    lock_dir = mock_workspaces / "sess-stale"
    lock_dir.mkdir()
    lock_file = lock_dir / ".lock"
    lock_file.write_text("1", encoding="utf-8")

    with patch("miniagent.assistant.engine.session_lock.is_process_running", return_value=False):
        ok, reason = try_lock_session("sess-stale")
    assert ok
    assert lock_file.read_text(encoding="utf-8") == str(os.getpid())


def test_try_lock_corrupt_lock_file(mock_workspaces: Path) -> None:
    lock_dir = mock_workspaces / "sess-corrupt"
    lock_dir.mkdir()
    lock_file = lock_dir / ".lock"
    lock_file.write_text("not-a-pid", encoding="utf-8")

    ok, reason = try_lock_session("sess-corrupt")
    assert ok
    assert lock_file.read_text(encoding="utf-8") == str(os.getpid())


def test_try_lock_empty_lock_file(mock_workspaces: Path) -> None:
    lock_dir = mock_workspaces / "sess-empty"
    lock_dir.mkdir()
    lock_file = lock_dir / ".lock"
    lock_file.write_text("", encoding="utf-8")

    ok, reason = try_lock_session("sess-empty")
    assert ok
    assert lock_file.read_text(encoding="utf-8") == str(os.getpid())


def test_try_lock_safe_session_id_path(mock_workspaces: Path) -> None:
    ok, reason = try_lock_session("feishu:oc_abc123")
    assert ok
    assert reason == ""
    assert (mock_workspaces / "feishu_oc_abc123" / ".lock").exists()


@pytest.mark.asyncio
async def test_try_lock_session_async_success(mock_workspaces: Path) -> None:
    ok, reason = await try_lock_session_async("sess-async")
    assert ok
    assert reason == ""
    assert (mock_workspaces / "sess-async" / ".lock").exists()


@pytest.mark.asyncio
async def test_try_lock_session_async_idempotent(mock_workspaces: Path) -> None:
    await try_lock_session_async("sess-async-idem")
    ok, reason = await try_lock_session_async("sess-async-idem")
    assert ok
    assert reason == ""


@pytest.mark.asyncio
async def test_try_lock_session_async_conflict(mock_workspaces: Path) -> None:
    lock_dir = mock_workspaces / "sess-async-conflict"
    lock_dir.mkdir()
    lock_file = lock_dir / ".lock"
    lock_file.write_text("999999", encoding="utf-8")

    with patch("miniagent.assistant.engine.session_lock.is_process_running_async", return_value=True):
        ok, reason = await try_lock_session_async("sess-async-conflict")
    assert not ok
    assert "999999" in reason


@pytest.mark.asyncio
async def test_try_lock_session_async_stale_pid(mock_workspaces: Path) -> None:
    lock_dir = mock_workspaces / "sess-async-stale"
    lock_dir.mkdir()
    lock_file = lock_dir / ".lock"
    lock_file.write_text("1", encoding="utf-8")

    with patch("miniagent.assistant.engine.session_lock.is_process_running_async", return_value=False):
        ok, reason = await try_lock_session_async("sess-async-stale")
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

    with patch("miniagent.assistant.engine.session_lock.is_process_running", return_value=True):
        pid = is_session_locked("sess-other2")
    assert pid == 999999


def test_is_session_locked_stale_returns_none(mock_workspaces: Path) -> None:
    lock_dir = mock_workspaces / "sess-stale-check"
    lock_dir.mkdir()
    lock_file = lock_dir / ".lock"
    lock_file.write_text("1", encoding="utf-8")

    with patch("miniagent.assistant.engine.session_lock.is_process_running", return_value=False):
        pid = is_session_locked("sess-stale-check")
    assert pid is None
    assert lock_file.exists()


def test_is_process_running_current() -> None:
    assert is_process_running(os.getpid())


@pytest.mark.asyncio
async def test_is_process_running_async_current() -> None:
    """Current PID checks must not depend on an external process listing."""
    assert await is_process_running_async(os.getpid()) is True


def test_is_process_running_fake() -> None:
    assert not is_process_running(999999999)
