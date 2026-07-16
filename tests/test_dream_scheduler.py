"""Tests for miniagent.assistant.memory.dream_scheduler — scheduling state and throttle."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from miniagent.assistant.memory import dream_scheduler
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


@pytest.mark.asyncio
async def test_scheduler_runs_complete_locked_refinement_in_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scheduler = dream_scheduler.DreamScheduler(str(tmp_path))
    scheduler._policy = dream_scheduler._DreamPolicy(0, 0, 0, 1, 0)
    calls: list[str] = []
    monkeypatch.setattr(dream_scheduler, "_try_file_lock", lambda _root: True)
    monkeypatch.setattr(
        dream_scheduler,
        "_refine_session_sync",
        lambda *_args: calls.append("refine"),
    )
    monkeypatch.setattr(
        dream_scheduler,
        "_release_file_lock",
        lambda _root: calls.append("release"),
    )

    scheduler.schedule("session")
    await asyncio.gather(*tuple(scheduler._pending_tasks))

    assert calls == ["refine", "release"]
    assert scheduler._pending_tasks == set()


def test_dream_constants() -> None:
    """Verify default constants are positive integers."""
    assert dream_scheduler.DIARY_REFINE_SEC > 0
    assert dream_scheduler.SESSION_LT_REFINE_SEC > 0
    assert dream_scheduler.AGENT_LT_REFINE_SEC > 0
    assert dream_scheduler.SIZE_FORCE_BYTES > 0


def test_diary_size_and_file_lock_lifecycle(tmp_path: Path) -> None:
    from miniagent.assistant.utils.session_id import safe_session_id

    diary = tmp_path / "memory" / "diary" / safe_session_id("session")
    diary.mkdir(parents=True)
    (diary / "a.md").write_bytes(b"1234")
    (diary / "b.md").write_bytes(b"56")
    assert dream_scheduler._diary_dir_size("session", str(tmp_path)) == 6
    assert dream_scheduler._try_file_lock(str(tmp_path))
    lock = tmp_path / "memory" / "dream.lock"
    assert lock.exists()
    dream_scheduler._release_file_lock(str(tmp_path))
    assert not lock.exists()


@pytest.mark.asyncio
async def test_refine_session_updates_all_memory_layers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rollups: list[dict] = []
    session_saves: list[dict] = []
    agent_saves: list[dict] = []
    monkeypatch.setattr(dream_scheduler, "DIARY_REFINE_SEC", 0)
    monkeypatch.setattr(dream_scheduler, "SESSION_LT_REFINE_SEC", 0)
    monkeypatch.setattr(dream_scheduler, "AGENT_LT_REFINE_SEC", 0)
    monkeypatch.setattr(dream_scheduler, "_diary_dir_size", lambda *_args: 10)
    monkeypatch.setattr(
        dream_scheduler,
        "append_session_day_rollup",
        lambda session_key, **kwargs: rollups.append({"session_key": session_key, **kwargs}),
    )
    monkeypatch.setattr(
        dream_scheduler,
        "load_session_longterm",
        lambda _session: {"day_entries": list(range(250))},
    )
    monkeypatch.setattr(
        dream_scheduler,
        "save_session_longterm",
        lambda _session, document: session_saves.append(document),
    )
    monkeypatch.setattr(
        dream_scheduler,
        "load_agent_longterm",
        lambda: {"entries": list(range(600))},
    )
    monkeypatch.setattr(
        dream_scheduler,
        "save_agent_longterm",
        lambda document: agent_saves.append(document),
    )

    await dream_scheduler._refine_session("session", str(tmp_path))
    assert rollups
    assert len(session_saves[0]["day_entries"]) == 120
    assert len(agent_saves[0]["entries"]) == 300
    state = dream_scheduler._load_dream_state(str(tmp_path))
    assert "session" in state["per_session"]


def test_scheduler_without_running_loop_is_noop(tmp_path: Path) -> None:
    scheduler = dream_scheduler.DreamScheduler(str(tmp_path))
    scheduler.schedule(None)
    scheduler.schedule("session")
    assert scheduler._pending_tasks == set()
