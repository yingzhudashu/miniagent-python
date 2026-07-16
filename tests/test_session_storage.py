"""Session disk storage preserves the existing config and history formats."""

from __future__ import annotations

import json

from miniagent.assistant.infrastructure.state_schemas import install_builtin_state_schemas
from miniagent.assistant.session.storage import SessionConfig, SessionDiskStorage


def _config(tmp_path, session_id: str = "s1") -> SessionConfig:
    workspace = tmp_path / session_id
    workspace.mkdir(parents=True)
    return SessionConfig(
        session_id=session_id,
        workspace_path=str(workspace),
        files_path=str(workspace / "files"),
        skills_path=str(workspace / "skills"),
        created_at="2026-01-01T00:00:00+00:00",
        last_active="2026-01-01T00:00:00+00:00",
        session_number=7,
        title="title",
    )


def test_storage_round_trips_current_config_schema(tmp_path) -> None:
    install_builtin_state_schemas()
    storage = SessionDiskStorage(str(tmp_path), config_cache_max=8)
    config = _config(tmp_path)
    storage.save_config(config)

    document = json.loads((tmp_path / "s1" / "config.json").read_text(encoding="utf-8"))
    assert document["schema_version"] == 1
    assert document["session_id"] == "s1"
    assert document["session_number"] == 7
    assert [entry.session_id for entry in storage.scan_configs()] == ["s1"]


def test_storage_ignores_invalid_config_without_breaking_scan(tmp_path) -> None:
    install_builtin_state_schemas()
    invalid = tmp_path / "invalid"
    invalid.mkdir()
    (invalid / "config.json").write_text("not-json", encoding="utf-8")
    storage = SessionDiskStorage(str(tmp_path), config_cache_max=8)
    assert storage.scan_configs() == []
    assert storage.scan_configs() == []


def test_storage_round_trips_and_bounds_current_history_schema(tmp_path) -> None:
    install_builtin_state_schemas()
    storage = SessionDiskStorage(str(tmp_path), config_cache_max=8)
    config = _config(tmp_path)
    history = [
        {"role": "user" if index % 2 == 0 else "assistant", "content": str(index)}
        for index in range(12)
    ]
    storage.save_history(config, history)
    document = json.loads((tmp_path / "s1" / "history.json").read_text(encoding="utf-8"))
    assert document["schema_version"] == 2
    assert document["message_format"] == "miniagent-conversation-v1"
    assert storage.load_history(config, max_messages=4) == history[-4:]
