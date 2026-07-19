"""Session disk storage preserves the existing config and history formats."""

from __future__ import annotations

import json
from unittest.mock import patch

from miniagent.assistant.infrastructure.state_schemas import install_builtin_state_schemas
from miniagent.assistant.session.storage import (
    SessionConfig,
    SessionDiskStorage,
    load_history_json_file,
    truncate_history,
)


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


def test_storage_preserves_system_and_first_user_when_truncating() -> None:
    history = [
        {"role": "system", "content": "policy"},
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "old"},
        {"role": "user", "content": "latest"},
    ]
    assert truncate_history(history, max_messages=3) == [history[0], history[1], history[3]]
    assert truncate_history(history, max_messages=99) is history
    assert truncate_history(history, max_messages=0) == history[:2]


def test_storage_handles_missing_corrupt_and_large_history(tmp_path) -> None:
    missing = tmp_path / "missing.json"
    assert load_history_json_file(str(missing)) == []
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("not-json", encoding="utf-8")
    assert load_history_json_file(str(corrupt)) == []


def test_storage_scans_cache_entries_and_lists_session_ids(tmp_path) -> None:
    install_builtin_state_schemas()
    storage = SessionDiskStorage(str(tmp_path), config_cache_max=1)
    storage.ensure_dir()
    config = _config(tmp_path, "one")
    storage.save_config(config)
    (tmp_path / "ordinary.txt").write_text("x", encoding="utf-8")
    assert [item.session_id for item in storage.scan_configs()] == ["one"]
    assert [item.session_id for item in storage.scan_configs()] == ["one"]
    assert storage.list_session_ids() == ["one"]
    (tmp_path / "one" / "config.json").unlink()
    assert storage.scan_configs() == []
    assert storage.config_cache == {}

    missing = SessionDiskStorage(str(tmp_path / "absent"), config_cache_max=1)
    assert missing.scan_configs() == []
    assert missing.list_session_ids() == []


def test_storage_save_and_cache_stat_failures_are_nonfatal(tmp_path) -> None:
    storage = SessionDiskStorage(str(tmp_path), config_cache_max=1)
    config = _config(tmp_path)
    with patch(
        "miniagent.assistant.session.storage.atomic_dump_json",
        side_effect=OSError("disk"),
    ):
        storage.save_config(config)
    assert storage.config_cache == {}

    config_path = str(tmp_path / "s1" / "config.json")
    storage.config_cache[config_path] = object()  # type: ignore[assignment]
    with patch("miniagent.assistant.session.storage.os.stat", side_effect=OSError("gone")):
        storage._cache_saved_config(config_path, config, cache_max=1)
    assert config_path not in storage.config_cache
