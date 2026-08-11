"""Lifecycle and persistence tests for Dream maintenance."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio

from miniagent.assistant.memory import dream_scheduler
from miniagent.assistant.memory.layered_memory import LongTermMemoryStore
from miniagent.assistant.state import StateStore
from tests.support.config import install_test_config


@pytest.fixture(autouse=True)
def isolate_state(tmp_path: Path) -> None:
    install_test_config(tmp_path, {"paths": {"state_dir": str(tmp_path)}})


@pytest_asyncio.fixture
async def dream_runtime(tmp_path: Path):
    async with StateStore(tmp_path) as state_store:
        longterm = LongTermMemoryStore(state_store)
        scheduler = dream_scheduler.DreamScheduler(str(tmp_path), state_store, longterm)
        yield scheduler, state_store, longterm
        await scheduler.shutdown()


@pytest.mark.asyncio
async def test_maintenance_state_round_trip(dream_runtime) -> None:
    _, state_store, _ = dream_runtime
    assert await state_store.load_maintenance_state("memory:dream") is None
    await state_store.save_maintenance_state("memory:dream", {"complete": True})
    assert await state_store.load_maintenance_state("memory:dream") == {"complete": True}


@pytest.mark.asyncio
async def test_scheduler_throttles_and_shuts_down(dream_runtime) -> None:
    scheduler, _, _ = dream_runtime
    scheduler.schedule("test-session")
    scheduler.schedule("test-session")
    assert len(scheduler._pending_tasks) <= 1
    await scheduler.shutdown()
    assert scheduler._pending_tasks == set()


@pytest.mark.asyncio
async def test_scheduler_runs_locked_refinement(
    dream_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler, _, _ = dream_runtime
    scheduler._policy = dream_scheduler._DreamPolicy(0, 0, 0, 1, 0)
    calls: list[str] = []

    async def refine(*_args) -> None:
        calls.append("refine")

    monkeypatch.setattr(dream_scheduler, "_refine_session", refine)
    scheduler.schedule("session")
    await asyncio.gather(*tuple(scheduler._pending_tasks))

    assert calls == ["refine"]
    assert scheduler._pending_tasks == set()


def test_dream_constants_are_positive() -> None:
    assert dream_scheduler.DIARY_REFINE_SEC > 0
    assert dream_scheduler.SESSION_LT_REFINE_SEC > 0
    assert dream_scheduler.AGENT_LT_REFINE_SEC > 0
    assert dream_scheduler.SIZE_FORCE_BYTES > 0


def test_diary_size_counts_only_files(tmp_path: Path) -> None:
    from miniagent.assistant.utils.session_id import safe_session_id

    diary = tmp_path / "memory" / "diary" / safe_session_id("session")
    diary.mkdir(parents=True)
    (diary / "a.md").write_bytes(b"1234")
    (diary / "b.md").write_bytes(b"56")
    (diary / "nested").mkdir()
    assert dream_scheduler._diary_dir_size("session", str(tmp_path)) == 6


@pytest.mark.asyncio
async def test_refine_updates_profiles_and_cursor(
    tmp_path: Path,
    dream_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, state_store, longterm = dream_runtime
    monkeypatch.setattr(dream_scheduler, "_diary_dir_size", lambda *_args: 10)
    await longterm.save_session(
        "session",
        {"day_entries": [{"day": str(index)} for index in range(250)]},
    )
    await longterm.save_agent({"entries": [{"text": str(index)} for index in range(600)]})

    policy = dream_scheduler._DreamPolicy(0, 0, 0, 1, 0)
    await dream_scheduler._refine_session(
        "session",
        str(tmp_path),
        state_store,
        longterm,
        policy,
    )

    session = await longterm.load_session("session")
    agent = await longterm.load_agent()
    state = await state_store.load_maintenance_state("memory:dream")
    assert len(session["day_entries"]) <= 121
    assert len(agent["entries"]) == 300
    assert state is not None and "session" in state["per_session"]


def test_scheduler_without_running_loop_is_noop(tmp_path: Path) -> None:
    state_store = StateStore(tmp_path)
    longterm = LongTermMemoryStore(state_store)
    scheduler = dream_scheduler.DreamScheduler(str(tmp_path), state_store, longterm)
    scheduler.schedule(None)
    scheduler.schedule("session")
    assert scheduler._pending_tasks == set()
