"""Tests for miniagent.memory.layered_memory — session/agent long-term memory."""

from __future__ import annotations

from pathlib import Path

import pytest

from miniagent.memory import layered_memory
from miniagent.memory.layered_memory import (
    append_session_day_rollup,
    load_agent_longterm,
    load_session_longterm,
    save_agent_longterm,
    save_session_longterm,
)


@pytest.fixture(autouse=True)
def isolate_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect MINI_AGENT_STATE to tmp_path so tests don't touch real workspaces."""
    monkeypatch.setenv("MINI_AGENT_STATE", str(tmp_path))


# ─── session long-term ───


def test_load_session_longterm_missing_file() -> None:
    doc = load_session_longterm("nonexistent")
    assert doc == {"session_key": "nonexistent", "day_entries": []}


def test_save_and_load_session_longterm() -> None:
    save_session_longterm("sess1", {"summary": "test"})
    doc = load_session_longterm("sess1")
    assert doc["session_key"] == "sess1"
    assert doc["summary"] == "test"
    assert "updated_at" in doc


def test_append_session_day_rollup() -> None:
    append_session_day_rollup(
        "sess2",
        day="2026-05-20",
        diary_relative="memory/diaries/sess2/2026-05-20.md",
        summary="Day 1",
    )
    doc = load_session_longterm("sess2")
    assert len(doc["day_entries"]) == 1
    entry = doc["day_entries"][0]
    assert entry["day"] == "2026-05-20"
    assert entry["summary"] == "Day 1"


def test_session_lt_path_isolated(tmp_path: Path) -> None:
    p = layered_memory._session_lt_path("test/key?with*bad")
    assert "test_key_with_bad" in p
    assert str(tmp_path) in p or "workspaces" in p


# ─── agent long-term ───


def test_load_agent_longterm_missing_file() -> None:
    doc = load_agent_longterm()
    assert doc == {"entries": []}


def test_save_and_load_agent_longterm() -> None:
    save_agent_longterm({"entries": [{"text": "fact"}]})
    doc = load_agent_longterm()
    assert len(doc["entries"]) == 1
    assert doc["entries"][0]["text"] == "fact"
    assert "updated_at" in doc
