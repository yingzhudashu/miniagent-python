from __future__ import annotations

import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from miniagent.assistant.state import STATE_SCHEMA_VERSION, StateSchemaError
from miniagent.assistant.state.registry import (
    REGISTRY_DATABASE_NAME,
    ProcessInstanceConflictError,
    ProcessInstanceStore,
    open_registry_database,
)


def _register(
    store: ProcessInstanceStore,
    *,
    project_dir: str,
    pid: int,
    now_ms: int,
):
    return store.register(
        project_dir=project_dir,
        project_key=f"project-{pid}",
        project_state_dir=f"C:/state/{pid}",
        pid=pid,
        mode="cli",
        active_sessions=("session",),
        hostname="test",
        now_ms=now_ms,
        alive_pid=lambda _pid: True,
        stale_before_ms=now_ms - 30_000,
    )


def test_empty_directory_creates_exact_registry_v5_schema(tmp_path: Path) -> None:
    ProcessInstanceStore(tmp_path)
    with open_registry_database(tmp_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == STATE_SCHEMA_VERSION
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(process_instances)")
        }
    assert {"instance_id", "project_dir", "pid", "heartbeat_at_ms"} <= columns


def test_registry_rejects_unversioned_old_or_incomplete_database(tmp_path: Path) -> None:
    path = tmp_path / REGISTRY_DATABASE_NAME
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE legacy_instances(id INTEGER)")
    connection.commit()
    connection.close()
    with pytest.raises(StateSchemaError, match="unversioned non-empty"):
        ProcessInstanceStore(tmp_path)

    path.unlink()
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA user_version=4")
    connection.commit()
    connection.close()
    with pytest.raises(StateSchemaError, match="schema v4"):
        ProcessInstanceStore(tmp_path)


def test_old_instance_directory_is_ignored(tmp_path: Path) -> None:
    legacy = tmp_path / "instances" / "1" / "meta.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b'{"pid":123}')
    store = ProcessInstanceStore(tmp_path)
    assert store.list_live(alive_pid=lambda _pid: True, stale_before_ms=0) == []
    assert legacy.read_bytes() == b'{"pid":123}'


def test_concurrent_project_registration_has_one_winner(tmp_path: Path) -> None:
    ProcessInstanceStore(tmp_path)
    now_ms = int(time.time() * 1000)

    def attempt(pid: int) -> str:
        store = ProcessInstanceStore(tmp_path)
        try:
            _register(store, project_dir="C:/same-project", pid=pid, now_ms=now_ms)
        except ProcessInstanceConflictError:
            return "conflict"
        return "winner"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(attempt, (101, 102)))
    assert sorted(outcomes) == ["conflict", "winner"]


def test_heartbeat_updates_are_stable_and_stale_rows_are_reclaimed(tmp_path: Path) -> None:
    store = ProcessInstanceStore(tmp_path)
    instance = _register(store, project_dir="C:/project", pid=101, now_ms=10_000)

    with ThreadPoolExecutor(max_workers=4) as executor:
        assert all(
            executor.map(
                lambda value: store.heartbeat(instance.instance_id, value),
                (10_001, 10_002, 10_003, 10_004),
            )
        )
    refreshed = store.get(instance.instance_id)
    assert refreshed is not None
    assert refreshed.heartbeat_at_ms == 10_004

    assert store.heartbeat(instance.instance_id, 9_000)
    unchanged = store.get(instance.instance_id)
    assert unchanged is not None
    assert unchanged.heartbeat_at_ms == 10_004

    assert store.list_live(alive_pid=lambda _pid: True, stale_before_ms=20_000) == []
    replacement = _register(store, project_dir="C:/project", pid=102, now_ms=30_000)
    assert replacement.instance_id == 1
