"""Tests for miniagent.infrastructure.debug_ndjson."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

from miniagent.infrastructure import debug_ndjson
from tests.config_helpers import install_test_config


def test_disabled_without_session_id(tmp_path: Path) -> None:
    """When debug.session_id is not set, agent_debug_log is a no-op."""
    install_test_config(tmp_path, {})
    importlib.reload(debug_ndjson)
    debug_ndjson.agent_debug_log(hypothesis_id="H1", location="test", message="noop")
    # No exception means success (no-op path)


def test_writes_when_enabled(tmp_path: Path) -> None:
    """When session is set, log entries are written."""
    log_file = tmp_path / "debug-test.log"
    install_test_config(
        tmp_path,
        {"debug": {"session_id": "test123", "log_path": str(log_file)}},
    )
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
