"""Strict current state persistence tests; no runtime migrations exist."""

from __future__ import annotations

import json

import pytest

from miniagent.assistant.infrastructure.persistence import (
    StateSchemaError,
    dump_state_file,
    load_state_file,
    validate_state,
)


@pytest.mark.parametrize(
    "payload",
    [[], {}, {"version": 1}, {"schema_version": 0}, {"schema_version": 2}],
)
def test_old_or_invalid_session_config_is_rejected(payload) -> None:
    with pytest.raises(StateSchemaError):
        validate_state("session_config", payload)


def test_invalid_file_is_not_rewritten_or_backed_up(tmp_path) -> None:
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"version": 1, "sessions": []}), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(StateSchemaError):
        load_state_file("session_config", path)
    assert path.read_bytes() == before
    assert not list(tmp_path.glob("*.bak"))


def test_writer_stamps_only_current_schema_metadata(tmp_path) -> None:
    path = tmp_path / "state.json"
    dump_state_file("scheduled_tasks", path, {"tasks": [], "version": 9})
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["schema_version"] == 2
    assert "version" not in document


def test_current_document_round_trips(tmp_path) -> None:
    path = tmp_path / "state.json"
    dump_state_file("session_config", path, {"sessions": []})
    assert load_state_file("session_config", path) == {
        "schema_version": 1,
        "sessions": [],
    }
