"""Session storage uses the current SQLite schema."""

from __future__ import annotations

from miniagent.assistant.session.storage import (
    SessionConfig,
    SessionStorage,
    truncate_history,
)


def _storage(tmp_path) -> SessionStorage:
    return SessionStorage(str(tmp_path / "sessions"))


def _config(tmp_path, session_id: str = "s1") -> SessionConfig:
    workspace = tmp_path / "sessions" / session_id
    workspace.mkdir(parents=True, exist_ok=True)
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


def test_storage_round_trips_config_without_legacy_json(tmp_path) -> None:
    storage = _storage(tmp_path)
    config = _config(tmp_path)
    storage.save_config(config)

    restored = storage.get_config("s1")
    assert restored is not None
    assert restored.session_id == "s1"
    assert restored.session_number == 7
    assert [entry.session_id for entry in storage.scan_configs()] == ["s1"]
    assert not (tmp_path / "sessions" / "s1" / "config.json").exists()


def test_storage_round_trips_and_bounds_history(tmp_path) -> None:
    storage = _storage(tmp_path)
    config = _config(tmp_path)
    storage.save_config(config)
    history = [
        {"role": "user" if index % 2 == 0 else "assistant", "content": str(index)}
        for index in range(12)
    ]
    storage.save_history(config, history)
    assert storage.load_history(config, max_messages=4) == history[-4:]
    assert not (tmp_path / "sessions" / "s1" / "history.json").exists()


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


def test_storage_lists_and_deletes_sessions(tmp_path) -> None:
    storage = _storage(tmp_path)
    storage.ensure_dir()
    storage.save_config(_config(tmp_path, "one"))
    storage.save_config(_config(tmp_path, "two"))
    assert storage.list_session_ids() == ["one", "two"]
    assert storage.delete_session("one") is True
    assert storage.delete_session("one") is False
    assert storage.list_session_ids() == ["two"]
