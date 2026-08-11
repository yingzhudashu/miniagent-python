"""Contract tests for SQLite-backed layered long-term memory."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio

from miniagent.assistant.memory.layered_memory import LongTermMemoryStore
from miniagent.assistant.state import StateStore


@pytest_asyncio.fixture
async def longterm(tmp_path: Path):
    """Provide one open profile store with application-like ownership."""
    async with StateStore(tmp_path) as state_store:
        yield LongTermMemoryStore(state_store)


@pytest.mark.asyncio
async def test_missing_profiles_return_current_empty_shapes(longterm) -> None:
    assert await longterm.load_session("missing") == {
        "session_key": "missing",
        "day_entries": [],
    }
    assert await longterm.load_agent() == {"entries": []}


@pytest.mark.asyncio
async def test_session_profile_round_trip(longterm) -> None:
    await longterm.save_session("sess1", {"summary": "test"})
    document = await longterm.load_session("sess1")
    assert document["session_key"] == "sess1"
    assert document["summary"] == "test"
    assert "updated_at" in document


@pytest.mark.asyncio
async def test_append_session_day_rollup(longterm) -> None:
    await longterm.append_session_day_rollup(
        "sess2",
        day="2026-05-20",
        diary_relative="memory/diary/sess2/2026-05-20.md",
        summary="Day 1",
    )
    document = await longterm.load_session("sess2")
    assert document["day_entries"] == [
        {
            "day": "2026-05-20",
            "diary_path": "memory/diary/sess2/2026-05-20.md",
            "summary": "Day 1",
            "added_at": document["day_entries"][0]["added_at"],
        }
    ]


@pytest.mark.asyncio
async def test_agent_profile_promote_and_remove(longterm) -> None:
    await longterm.promote("keep", source_session="main")
    await longterm.promote("remove", source_session="__bg__task")
    assert await longterm.remove_agent_entries_for_session("__bg__task") == 1
    document = await longterm.load_agent()
    assert [entry["text"] for entry in document["entries"]] == ["keep"]


@pytest.mark.asyncio
async def test_retired_json_is_not_read_or_modified(tmp_path: Path) -> None:
    legacy = tmp_path / "memory" / "session_lt" / "legacy.json"
    legacy.parent.mkdir(parents=True)
    payload = {"summary": "must not import"}
    legacy.write_text(json.dumps(payload), encoding="utf-8")

    async with StateStore(tmp_path) as state_store:
        longterm = LongTermMemoryStore(state_store)
        assert await longterm.load_session("legacy") == {
            "session_key": "legacy",
            "day_entries": [],
        }

    assert json.loads(legacy.read_text(encoding="utf-8")) == payload
