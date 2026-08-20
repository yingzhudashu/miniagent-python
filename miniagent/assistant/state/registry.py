"""Strict SQLite storage for the global process-instance registry."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from miniagent.assistant.state import StateSchemaError

REGISTRY_DATABASE_NAME = "registry.sqlite3"
REGISTRY_SCHEMA_VERSION = 5

_REGISTRY_SCHEMA = """
CREATE TABLE process_instances (
    instance_id INTEGER PRIMARY KEY CHECK (instance_id > 0),
    project_dir TEXT NOT NULL UNIQUE,
    project_key TEXT NOT NULL,
    project_state_dir TEXT NOT NULL,
    pid INTEGER NOT NULL CHECK (pid > 0),
    mode TEXT NOT NULL CHECK (mode IN ('cli', 'both')),
    active_sessions_json TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(active_sessions_json)),
    hostname TEXT NOT NULL,
    started_at_ms INTEGER NOT NULL,
    heartbeat_at_ms INTEGER NOT NULL
) STRICT;
"""

_REGISTRY_COLUMNS = frozenset(
    {
        "instance_id",
        "project_dir",
        "project_key",
        "project_state_dir",
        "pid",
        "mode",
        "active_sessions_json",
        "hostname",
        "started_at_ms",
        "heartbeat_at_ms",
    }
)


def _validate(connection: sqlite3.Connection) -> None:
    try:
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        objects = {
            (str(row[0]), str(row[1]))
            for row in connection.execute(
                "SELECT name, type FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
            )
        }
    except sqlite3.DatabaseError as error:
        raise StateSchemaError(f"invalid registry database: {error}") from error

    if version not in {0, REGISTRY_SCHEMA_VERSION}:
        raise StateSchemaError(
            f"registry database uses schema v{version}; required schema v{REGISTRY_SCHEMA_VERSION}"
        )
    if not objects:
        try:
            connection.executescript(
                "BEGIN IMMEDIATE;\n"
                + _REGISTRY_SCHEMA
                + f"\nPRAGMA user_version={REGISTRY_SCHEMA_VERSION};\nCOMMIT;"
            )
        except sqlite3.DatabaseError as error:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise StateSchemaError(f"cannot create registry database: {error}") from error
        return
    if version == 0:
        raise StateSchemaError("unversioned non-empty registry database")
    if objects != {("process_instances", "table")}:
        unexpected = ", ".join(sorted(name for name, _kind in objects))
        raise StateSchemaError(f"unexpected registry database objects: {unexpected}")
    actual = frozenset(
        str(row[1]) for row in connection.execute("PRAGMA table_info(process_instances)")
    )
    if actual != _REGISTRY_COLUMNS:
        raise StateSchemaError("registry database has invalid process_instances columns")


@contextmanager
def open_registry_database(state_dir: str | Path) -> Iterator[sqlite3.Connection]:
    """Open the exact v5 registry database using short-lived connections."""
    root = Path(state_dir)
    root.mkdir(parents=True, exist_ok=True)
    try:
        connection = sqlite3.connect(
            root / REGISTRY_DATABASE_NAME,
            timeout=5.0,
            isolation_level=None,
        )
    except sqlite3.DatabaseError as error:
        raise StateSchemaError(f"cannot open registry database: {error}") from error
    connection.row_factory = sqlite3.Row
    try:
        _validate(connection)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA journal_mode=WAL")
        yield connection
    finally:
        connection.close()


@contextmanager
def _immediate(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    else:
        connection.execute("COMMIT")


@dataclass(frozen=True)
class ProcessInstance:
    """Typed row stored in ``process_instances``."""

    instance_id: int
    project_dir: str
    project_key: str
    project_state_dir: str
    pid: int
    mode: str
    active_sessions: tuple[str, ...]
    hostname: str
    started_at_ms: int
    heartbeat_at_ms: int

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> ProcessInstance:
        """Decode and validate one strict registry row."""
        sessions = json.loads(str(row["active_sessions_json"]))
        if not isinstance(sessions, list) or not all(isinstance(item, str) for item in sessions):
            raise StateSchemaError("process instance active_sessions must be strings")
        return cls(
            instance_id=int(row["instance_id"]),
            project_dir=str(row["project_dir"]),
            project_key=str(row["project_key"]),
            project_state_dir=str(row["project_state_dir"]),
            pid=int(row["pid"]),
            mode=str(row["mode"]),
            active_sessions=tuple(sessions),
            hostname=str(row["hostname"]),
            started_at_ms=int(row["started_at_ms"]),
            heartbeat_at_ms=int(row["heartbeat_at_ms"]),
        )


class ProcessInstanceConflictError(RuntimeError):
    """A live instance already owns the normalized project directory."""

    def __init__(self, existing: ProcessInstance) -> None:
        self.existing = existing
        super().__init__(existing.project_dir)


class ProcessInstanceStore:
    """Domain store for atomic instance registration and heartbeat updates."""

    def __init__(self, state_dir: str | Path) -> None:
        self.state_dir = Path(state_dir)
        # Validate or bootstrap immediately so invalid state fails at startup.
        with open_registry_database(self.state_dir):
            pass

    def register(
        self,
        *,
        project_dir: str,
        project_key: str,
        project_state_dir: str,
        pid: int,
        mode: str,
        active_sessions: Sequence[str],
        hostname: str,
        now_ms: int,
        alive_pid: Callable[[int], bool],
        stale_before_ms: int,
    ) -> ProcessInstance:
        """Atomically remove stale rows and register a non-conflicting process."""
        with open_registry_database(self.state_dir) as connection:
            with _immediate(connection):
                self._delete_stale(connection, alive_pid, stale_before_ms)
                conflict = connection.execute(
                    "SELECT * FROM process_instances WHERE project_dir=?",
                    (project_dir,),
                ).fetchone()
                if conflict is not None:
                    raise ProcessInstanceConflictError(ProcessInstance.from_row(conflict))
                row = connection.execute(
                    "SELECT COALESCE(MAX(instance_id), 0) + 1 FROM process_instances"
                ).fetchone()
                instance_id = int(row[0])
                connection.execute(
                    """INSERT INTO process_instances VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        instance_id,
                        project_dir,
                        project_key,
                        project_state_dir,
                        pid,
                        mode,
                        json.dumps(list(active_sessions), ensure_ascii=False),
                        hostname,
                        now_ms,
                        now_ms,
                    ),
                )
                stored = connection.execute(
                    "SELECT * FROM process_instances WHERE instance_id=?",
                    (instance_id,),
                ).fetchone()
        if stored is None:  # pragma: no cover - guarded by the transaction above
            raise RuntimeError("registered process instance disappeared")
        return ProcessInstance.from_row(stored)

    def list_live(
        self,
        *,
        alive_pid: Callable[[int], bool],
        stale_before_ms: int,
    ) -> list[ProcessInstance]:
        """Prune stale owners and return live instances in display order."""
        with open_registry_database(self.state_dir) as connection:
            with _immediate(connection):
                self._delete_stale(connection, alive_pid, stale_before_ms)
                rows = connection.execute(
                    "SELECT * FROM process_instances ORDER BY instance_id"
                ).fetchall()
        return [ProcessInstance.from_row(row) for row in rows]

    def get(self, instance_id: int) -> ProcessInstance | None:
        """Load one registered process by stable display identifier."""
        with open_registry_database(self.state_dir) as connection:
            row = connection.execute(
                "SELECT * FROM process_instances WHERE instance_id=?",
                (instance_id,),
            ).fetchone()
        return None if row is None else ProcessInstance.from_row(row)

    def heartbeat(self, instance_id: int, now_ms: int) -> bool:
        """Advance a process heartbeat monotonically."""
        with open_registry_database(self.state_dir) as connection:
            cursor = connection.execute(
                """UPDATE process_instances
                   SET heartbeat_at_ms=MAX(heartbeat_at_ms, ?)
                   WHERE instance_id=?""",
                (now_ms, instance_id),
            )
            return cursor.rowcount == 1

    def update_mode(self, instance_id: int, mode: str) -> bool:
        """Replace the advertised runtime mode for an existing process."""
        with open_registry_database(self.state_dir) as connection:
            cursor = connection.execute(
                "UPDATE process_instances SET mode=? WHERE instance_id=?",
                (mode, instance_id),
            )
            return cursor.rowcount == 1

    def update_sessions(self, instance_id: int, active_sessions: Sequence[str]) -> bool:
        """Replace the advertised active-session snapshot."""
        with open_registry_database(self.state_dir) as connection:
            cursor = connection.execute(
                "UPDATE process_instances SET active_sessions_json=? WHERE instance_id=?",
                (json.dumps(list(active_sessions), ensure_ascii=False), instance_id),
            )
            return cursor.rowcount == 1

    def delete(self, instance_id: int) -> bool:
        """Unregister one process and report whether it existed."""
        with open_registry_database(self.state_dir) as connection:
            cursor = connection.execute(
                "DELETE FROM process_instances WHERE instance_id=?",
                (instance_id,),
            )
            return cursor.rowcount == 1

    @staticmethod
    def _delete_stale(
        connection: sqlite3.Connection,
        alive_pid: Callable[[int], bool],
        stale_before_ms: int,
    ) -> None:
        rows = connection.execute(
            "SELECT instance_id, pid, heartbeat_at_ms FROM process_instances"
        ).fetchall()
        stale_ids = [
            int(row["instance_id"])
            for row in rows
            if int(row["heartbeat_at_ms"]) < stale_before_ms or not bool(alive_pid(int(row["pid"])))
        ]
        connection.executemany(
            "DELETE FROM process_instances WHERE instance_id=?",
            ((instance_id,) for instance_id in stale_ids),
        )


__all__ = [
    "ProcessInstance",
    "ProcessInstanceConflictError",
    "ProcessInstanceStore",
    "REGISTRY_DATABASE_NAME",
    "REGISTRY_SCHEMA_VERSION",
    "open_registry_database",
]
