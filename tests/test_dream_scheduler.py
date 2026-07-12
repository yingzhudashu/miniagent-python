"""Tests for miniagent.memory.dream_scheduler — scheduling state and throttle."""

from __future__ import annotations

from pathlib import Path

import pytest

from miniagent.memory import dream_scheduler
from tests.config_helpers import install_test_config


@pytest.fixture(autouse=True)
def isolate_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect paths.state_dir to tmp_path."""
    state_dir = str(tmp_path)
    monkeypatch.setenv("MINIAGENT_PATHS_STATE_DIR", state_dir)
    install_test_config(tmp_path, {"paths": {"state_dir": state_dir}})


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


@pytest.mark.asyncio
async def test_scheduler_throttles_and_shuts_down(tmp_path: Path) -> None:
    scheduler = dream_scheduler.DreamScheduler(str(tmp_path))
    scheduler.schedule("test-session")
    scheduler.schedule("test-session")
    assert len(scheduler._pending_tasks) <= 1
    await scheduler.shutdown()
    assert scheduler._pending_tasks == set()


def test_dream_constants() -> None:
    """Verify default constants are positive integers."""
    assert dream_scheduler.DIARY_REFINE_SEC > 0
    assert dream_scheduler.SESSION_LT_REFINE_SEC > 0
    assert dream_scheduler.AGENT_LT_REFINE_SEC > 0
    assert dream_scheduler.SIZE_FORCE_BYTES > 0
