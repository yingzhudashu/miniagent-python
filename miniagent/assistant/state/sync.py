"""Synchronous domain access for existing non-async product boundaries.

The CLI command and session-manager interfaces are intentionally synchronous.
They use short-lived SQLite connections here, while async services use
``StateStore``.  Both paths share the same exact schema and transaction rules.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from miniagent.assistant.state import (
    _DATABASE_NAME,
    _FTS_TABLES,
    _SCHEMA,
    _TABLE_COLUMNS,
    STATE_SCHEMA_VERSION,
    StateSchemaError,
)


def _validate(connection: sqlite3.Connection) -> None:
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    objects = {
        (str(row[0]), str(row[1]))
        for row in connection.execute(
            "SELECT name, type FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
        )
    }
    if version not in {0, STATE_SCHEMA_VERSION}:
        raise StateSchemaError(
            f"state database uses schema v{version}; required schema v{STATE_SCHEMA_VERSION}"
        )
    if not objects:
        try:
            connection.executescript(
                "BEGIN IMMEDIATE;\n"
                + _SCHEMA
                + f"\nPRAGMA user_version={STATE_SCHEMA_VERSION};\nCOMMIT;"
            )
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        return
    if version == 0:
        raise StateSchemaError("unversioned non-empty state database")
    allowed = set(_TABLE_COLUMNS) | set(_FTS_TABLES)
    for fts in _FTS_TABLES:
        allowed.update(f"{fts}_{suffix}" for suffix in ("data", "idx", "content", "docsize", "config"))
    for name, kind in objects:
        if kind not in {"table", "shadow"} or name not in allowed:
            raise StateSchemaError(f"unexpected {name} in state database")
    present = {name for name, _ in objects}
    missing = (set(_TABLE_COLUMNS) | set(_FTS_TABLES)) - present
    if missing:
        raise StateSchemaError(f"state database is missing {', '.join(sorted(missing))}")
    for table, expected in _TABLE_COLUMNS.items():
        actual = frozenset(
            str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")
        )
        if actual != expected:
            raise StateSchemaError(f"state database has invalid {table} columns")
    for table in _FTS_TABLES:
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name=? AND type='table'", (table,)
        ).fetchone()
        definition = str(row[0]) if row is not None and row[0] is not None else ""
        if "tokenize='trigram'" not in definition:
            raise StateSchemaError(
                f"state database {table} must use the FTS5 trigram tokenizer"
            )


@contextmanager
def open_state_database(state_dir: str | Path) -> Iterator[sqlite3.Connection]:
    """Open the exact 5.0 project database and close it after the operation."""
    root = Path(state_dir)
    root.mkdir(parents=True, exist_ok=True)
    try:
        connection = sqlite3.connect(
            root / _DATABASE_NAME, timeout=5.0, isolation_level=None
        )
    except sqlite3.DatabaseError as error:
        raise StateSchemaError(f"cannot open state database: {error}") from error
    connection.row_factory = sqlite3.Row
    try:
        try:
            _validate(connection)
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError as error:
            raise StateSchemaError(f"cannot open state database: {error}") from error
        yield connection
    finally:
        connection.close()


@contextmanager
def immediate_transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run one synchronous write atomically with early writer acquisition."""
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    else:
        connection.execute("COMMIT")


__all__ = ["immediate_transaction", "open_state_database"]
