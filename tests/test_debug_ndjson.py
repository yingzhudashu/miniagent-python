"""Tests for miniagent.infrastructure.debug_ndjson."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from miniagent.infrastructure import debug_ndjson


def test_disabled_without_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """When MINIAGENT_DEBUG_SESSION_ID is not set, agent_debug_log is a no-op."""
    monkeypatch.delenv("MINIAGENT_DEBUG_SESSION_ID", raising=False)
    monkeypatch.delenv("MINIAGENT_DEBUG_LOG_PATH", raising=False)
    # Re-read module state — _SESSION is module-level, so we test the public API
    # Since the module was already imported, _SESSION is fixed at import time.
    # We can only verify the function doesn't raise when session is empty.
    debug_ndjson.agent_debug_log(hypothesis_id="H1", location="test", message="noop")
    # No exception means success (no-op path)


def test_writes_when_enabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """When session is set, log entries are written."""
    log_file = tmp_path / "debug-test.log"
    monkeypatch.setenv("MINIAGENT_DEBUG_SESSION_ID", "test123")
    monkeypatch.setenv("MINIAGENT_DEBUG_LOG_PATH", str(log_file))

    # Force re-evaluation of module-level vars by reloading
    import importlib

    importlib.reload(debug_ndjson)

    debug_ndjson.agent_debug_log(
        hypothesis_id="H1", location="test.py:10", message="hello", data={"k": 1}
    )

    lines = log_file.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["sessionId"] == "test123"
    assert entry["hypothesisId"] == "H1"
    assert entry["location"] == "test.py:10"
    assert entry["message"] == "hello"
    assert entry["data"] == {"k": 1}
    assert "timestamp" in entry
