"""Tests for miniagent.memory.dream_scheduler — scheduling state and throttle."""

from __future__ import annotations

from pathlib import Path

import pytest

from miniagent.memory import dream_scheduler


@pytest.fixture(autouse=True)
def isolate_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect MINI_AGENT_STATE to tmp_path."""
    monkeypatch.setenv("MINI_AGENT_STATE", str(tmp_path))


def test_load_dream_state_missing() -> None:
    state = dream_scheduler._load_dream_state()
    assert state == {}


def test_save_and_load_dream_state(tmp_path: Path) -> None:
    dream_scheduler._save_dream_state({"last_refine": "2026-05-20"})
    state = dream_scheduler._load_dream_state()
    assert state["last_refine"] == "2026-05-20"


def test_state_path_creates_memory_dir(tmp_path: Path) -> None:
    p = dream_scheduler._state_path()
    assert p.endswith("dream_state.json")
    assert (tmp_path / "memory").is_dir()


def test_schedule_throttle() -> None:
    """schedule_memory_maintenance should not raise even when throttled."""
    # First call may schedule; second call within MIN_INTERVAL should be throttled
    dream_scheduler.schedule_memory_maintenance("test-session")
    # Should not raise
    dream_scheduler.schedule_memory_maintenance("test-session")


def test_dream_constants() -> None:
    """Verify default constants are positive integers."""
    assert dream_scheduler.DIARY_REFINE_SEC > 0
    assert dream_scheduler.SESSION_LT_REFINE_SEC > 0
    assert dream_scheduler.AGENT_LT_REFINE_SEC > 0
    assert dream_scheduler.SIZE_FORCE_BYTES > 0
