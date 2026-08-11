"""Transactional project state for the 5.0 runtime.

The store intentionally understands one schema version only.  A new, empty
database is bootstrapped as version 5; every other on-disk shape is rejected
instead of being migrated or interpreted as legacy state.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
from array import array
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite

STATE_SCHEMA_VERSION = 5
_DATABASE_NAME = "state.sqlite3"


class StateError(RuntimeError):
    """Base class for durable-state failures."""


class StateSchemaError(StateError):
    """The database does not match the exact current schema."""


class StateConflictError(StateError):
    """A transactional claim is owned by another process."""


_SCHEMA = """
CREATE TABLE sessions (
    session_id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    workspace_path TEXT NOT NULL DEFAULT '',
    files_path TEXT NOT NULL DEFAULT '',
    skills_path TEXT NOT NULL DEFAULT '',
    session_number INTEGER NOT NULL DEFAULT 0,
    chat_id TEXT,
    sender_id TEXT,
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    next_sequence INTEGER NOT NULL DEFAULT 1 CHECK (next_sequence >= 1)
) STRICT;

CREATE TABLE messages (
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    message_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content_json TEXT NOT NULL CHECK (json_valid(content_json)),
    created_at_ms INTEGER NOT NULL,
    PRIMARY KEY (session_id, sequence),
    UNIQUE (session_id, message_id)
) STRICT;

CREATE TABLE channel_bindings (
    channel_type TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata_json)),
    updated_at_ms INTEGER NOT NULL,
    PRIMARY KEY (channel_type, channel_id)
) STRICT;

CREATE TABLE cli_state (
    state_key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL CHECK (json_valid(value_json)),
    updated_at_ms INTEGER NOT NULL
) STRICT;

CREATE TABLE memory_profiles (
    scope TEXT NOT NULL,
    namespace TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata_json)),
    PRIMARY KEY (scope, namespace)
) STRICT;

CREATE TABLE memory_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_key TEXT NOT NULL UNIQUE,
    scope TEXT NOT NULL,
    namespace TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata_json)),
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    FOREIGN KEY (scope, namespace) REFERENCES memory_profiles(scope, namespace)
        ON DELETE CASCADE
) STRICT;

CREATE VIRTUAL TABLE memory_fts USING fts5(content, tokenize='trigram');

CREATE TABLE memory_embeddings (
    memory_id INTEGER NOT NULL REFERENCES memory_entries(id) ON DELETE CASCADE,
    model TEXT NOT NULL,
    text_hash TEXT NOT NULL DEFAULT '',
    dimension INTEGER NOT NULL CHECK (dimension > 0),
    vector_blob BLOB NOT NULL,
    norm REAL NOT NULL CHECK (norm > 0),
    PRIMARY KEY (memory_id, model)
) STRICT;

CREATE TABLE knowledge_mounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    source_path TEXT NOT NULL,
    updated_at_ms INTEGER NOT NULL
) STRICT;

CREATE TABLE knowledge_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mount_id INTEGER NOT NULL REFERENCES knowledge_mounts(id) ON DELETE CASCADE,
    relative_path TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata_json)),
    updated_at_ms INTEGER NOT NULL,
    UNIQUE (mount_id, relative_path)
) STRICT;

CREATE VIRTUAL TABLE knowledge_fts USING fts5(title, content, tokenize='trigram');

CREATE TABLE scheduled_tasks (
    task_id TEXT PRIMARY KEY,
    task_json TEXT NOT NULL CHECK (json_valid(task_json)),
    next_run_at_ms INTEGER,
    claim_owner TEXT,
    claim_until_ms INTEGER,
    updated_at_ms INTEGER NOT NULL
) STRICT;

CREATE TABLE feishu_message_claims (
    message_id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    claim_until_ms INTEGER,
    completed_at_ms INTEGER
) STRICT;

CREATE TABLE process_leases (
    resource TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    expires_at_ms INTEGER NOT NULL,
    generation INTEGER NOT NULL DEFAULT 1 CHECK (generation >= 1)
) STRICT;

CREATE TABLE maintenance_state (
    state_key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL CHECK (json_valid(value_json)),
    updated_at_ms INTEGER NOT NULL
) STRICT;
"""

_TABLE_COLUMNS: dict[str, frozenset[str]] = {
    "sessions": frozenset({"session_id", "title", "description", "workspace_path", "files_path", "skills_path", "session_number", "chat_id", "sender_id", "created_at_ms", "updated_at_ms", "next_sequence"}),
    "messages": frozenset({"session_id", "sequence", "message_id", "role", "content_json", "created_at_ms"}),
    "channel_bindings": frozenset({"channel_type", "channel_id", "session_id", "metadata_json", "updated_at_ms"}),
    "cli_state": frozenset({"state_key", "value_json", "updated_at_ms"}),
    "memory_profiles": frozenset({"scope", "namespace", "metadata_json"}),
    "memory_entries": frozenset({"id", "entry_key", "scope", "namespace", "content", "metadata_json", "created_at_ms", "updated_at_ms"}),
    "memory_embeddings": frozenset({"memory_id", "model", "text_hash", "dimension", "vector_blob", "norm"}),
    "knowledge_mounts": frozenset({"id", "name", "source_path", "updated_at_ms"}),
    "knowledge_documents": frozenset({"id", "mount_id", "relative_path", "title", "content", "content_hash", "metadata_json", "updated_at_ms"}),
    "scheduled_tasks": frozenset({"task_id", "task_json", "next_run_at_ms", "claim_owner", "claim_until_ms", "updated_at_ms"}),
    "feishu_message_claims": frozenset({"message_id", "owner", "claim_until_ms", "completed_at_ms"}),
    "process_leases": frozenset({"resource", "owner", "expires_at_ms", "generation"}),
    "maintenance_state": frozenset({"state_key", "value_json", "updated_at_ms"}),
}
_FTS_TABLES = frozenset({"memory_fts", "knowledge_fts"})


def _milliseconds(value: float | None = None) -> int:
    return int((time.time() if value is None else value) * 1000)


def _json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as error:
        raise StateSchemaError(f"state value is not valid JSON: {error}") from error


def memory_entry_key(scope: str, entry: dict[str, Any], ordinal: int = 0) -> str:
    """Build a stable key for one current-version durable memory entry."""
    canonical = _json(
        {
            "timestamp": str(entry.get("timestamp", "")),
            "user_snippet": str(entry.get("user_snippet", "")),
            "summary": str(entry.get("summary", "")),
            "facts": [str(item) for item in entry.get("facts", [])],
            "ordinal": int(ordinal),
        }
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]
    return f"{scope}:{digest}"


def _entry_created_ms(timestamp: str) -> int:
    try:
        return int(datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp() * 1000)
    except (TypeError, ValueError):
        return _milliseconds()


async def _fetchone(connection: aiosqlite.Connection, sql: str, parameters: Sequence[Any] = ()) -> aiosqlite.Row | None:
    cursor = await connection.execute(sql, parameters)
    try:
        return await cursor.fetchone()
    finally:
        await cursor.close()


class StateStore:
    """One explicitly owned asynchronous project-state database."""

    def __init__(self, state_dir: str | Path) -> None:
        self.state_dir = Path(state_dir)
        self.path = self.state_dir / _DATABASE_NAME
        self._connection: aiosqlite.Connection | None = None
        self._open_lock = asyncio.Lock()
        self._transaction_lock = asyncio.Lock()

    @property
    def connection(self) -> aiosqlite.Connection:
        """Return the owned open connection or fail on lifecycle misuse."""
        if self._connection is None:
            raise StateError("state store is not open")
        return self._connection

    async def __aenter__(self) -> StateStore:
        return await self.open()

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self.close()

    async def open(self) -> StateStore:
        """Open once and validate or bootstrap the exact schema 5 database."""
        if self._connection is not None:
            return self
        async with self._open_lock:
            if self._connection is not None:
                return self
            self.state_dir.mkdir(parents=True, exist_ok=True)
            connection = await aiosqlite.connect(self.path, isolation_level=None)
            connection.row_factory = aiosqlite.Row
            self._connection = connection
            try:
                await self._open_exact_schema()
            except aiosqlite.DatabaseError as error:
                await self.close()
                raise StateSchemaError(f"cannot open state database: {error}") from error
            except BaseException:
                await self.close()
                raise
        return self

    async def close(self) -> None:
        """Close the owned connection idempotently."""
        connection, self._connection = self._connection, None
        if connection is not None:
            await connection.close()

    async def _open_exact_schema(self) -> None:
        connection = self.connection
        version_row = await _fetchone(connection, "PRAGMA user_version")
        version = int(version_row[0]) if version_row is not None else 0
        objects = await connection.execute_fetchall(
            "SELECT name, type FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
        )
        user_objects = {(str(row[0]), str(row[1])) for row in objects}
        if version not in {0, STATE_SCHEMA_VERSION}:
            raise StateSchemaError(
                f"state database uses schema v{version}; required schema v{STATE_SCHEMA_VERSION}"
            )
        if not user_objects:
            await connection.execute("PRAGMA journal_mode=WAL")
            await connection.execute("PRAGMA synchronous=NORMAL")
            await connection.execute("PRAGMA foreign_keys=ON")
            await connection.execute("PRAGMA busy_timeout=5000")
            try:
                await connection.executescript(
                    "BEGIN IMMEDIATE;\n"
                    + _SCHEMA
                    + f"\nPRAGMA user_version={STATE_SCHEMA_VERSION};\nCOMMIT;"
                )
            except BaseException:
                if connection.in_transaction:
                    await connection.execute("ROLLBACK")
                raise
            return
        if version == 0:
            raise StateSchemaError("unversioned non-empty state database")
        await self._validate_objects(user_objects)
        await connection.execute("PRAGMA foreign_keys=ON")
        await connection.execute("PRAGMA busy_timeout=5000")
        await connection.execute("PRAGMA synchronous=NORMAL")
        await connection.execute("PRAGMA journal_mode=WAL")

    async def _validate_objects(self, objects: set[tuple[str, str]]) -> None:
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
            rows = await self.connection.execute_fetchall(f"PRAGMA table_info({table})")
            actual = frozenset(str(row[1]) for row in rows)
            if actual != expected:
                raise StateSchemaError(f"state database has invalid {table} columns")
        for table in _FTS_TABLES:
            row = await _fetchone(
                self.connection,
                "SELECT sql FROM sqlite_master WHERE name=? AND type='table'",
                (table,),
            )
            definition = str(row[0]) if row is not None and row[0] is not None else ""
            if "tokenize='trigram'" not in definition:
                raise StateSchemaError(
                    f"state database {table} must use the FTS5 trigram tokenizer"
                )

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        """Serialize ``BEGIN IMMEDIATE`` writes with rollback on every failure."""
        from miniagent.agent.observability import trace_span

        async with self._transaction_lock:
            with trace_span("state.transaction"):
                connection = self.connection
                await connection.execute("BEGIN IMMEDIATE")
                try:
                    yield connection
                except BaseException:
                    await connection.execute("ROLLBACK")
                    raise
                else:
                    await connection.execute("COMMIT")

    async def create_session(self, session_id: str, *, title: str = "", **fields: Any) -> None:
        """Insert one session using typed current-schema fields."""
        now = _milliseconds()
        await self.connection.execute(
            """INSERT INTO sessions(
                   session_id, title, description, workspace_path, files_path, skills_path,
                   session_number, chat_id, sender_id, created_at_ms, updated_at_ms
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                title,
                str(fields.get("description", "")),
                str(fields.get("workspace_path", "")),
                str(fields.get("files_path", "")),
                str(fields.get("skills_path", "")),
                int(fields.get("session_number", 0)),
                fields.get("chat_id"),
                fields.get("sender_id"),
                int(fields.get("created_at_ms", now)),
                int(fields.get("updated_at_ms", now)),
            ),
        )

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        """Return one raw typed session row or ``None``."""
        row = await _fetchone(self.connection, "SELECT * FROM sessions WHERE session_id=?", (session_id,))
        return dict(row) if row is not None else None

    async def append_message(self, session_id: str, message_id: str, role: str, content: Any) -> int:
        """Allocate and append the next per-session sequence atomically."""
        now = _milliseconds()
        async with self.transaction() as connection:
            row = await _fetchone(connection, "SELECT next_sequence FROM sessions WHERE session_id=?", (session_id,))
            if row is None:
                raise StateSchemaError(f"unknown session {session_id}")
            sequence = int(row[0])
            await connection.execute(
                "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, sequence, message_id, role, _json(content), now),
            )
            await connection.execute(
                "UPDATE sessions SET next_sequence=?, updated_at_ms=? WHERE session_id=?",
                (sequence + 1, now, session_id),
            )
        return sequence

    async def list_messages(self, session_id: str) -> list[dict[str, Any]]:
        """Return decoded messages in durable sequence order."""
        rows = await self.connection.execute_fetchall(
            "SELECT * FROM messages WHERE session_id=? ORDER BY sequence", (session_id,)
        )
        result = []
        for row in rows:
            item = dict(row)
            item["content"] = json.loads(item.pop("content_json"))
            result.append(item)
        return result

    async def bind_channel(self, channel_type: str, channel_id: str, session_id: str, *, metadata: Any | None = None) -> None:
        """Bind one channel address to its current session route."""
        await self.connection.execute(
            """INSERT INTO channel_bindings VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(channel_type, channel_id) DO UPDATE SET
                 session_id=excluded.session_id,
                 metadata_json=excluded.metadata_json,
                 updated_at_ms=excluded.updated_at_ms""",
            (channel_type, channel_id, session_id, _json(metadata or {}), _milliseconds()),
        )

    async def resolve_channel(self, channel_type: str, channel_id: str) -> str | None:
        """Resolve a channel address to its bound session identifier."""
        row = await _fetchone(
            self.connection,
            "SELECT session_id FROM channel_bindings WHERE channel_type=? AND channel_id=?",
            (channel_type, channel_id),
        )
        return str(row[0]) if row is not None else None

    async def list_scheduled_tasks(self) -> list[dict[str, Any]]:
        """Return decoded task definitions in stable identifier order."""
        rows = await self.connection.execute_fetchall("SELECT task_json FROM scheduled_tasks ORDER BY task_id")
        return [json.loads(str(row[0])) for row in rows]

    async def load_memory_profile(
        self,
        scope: str,
        namespace: str,
    ) -> dict[str, Any] | None:
        """Return one typed memory profile without interpreting another namespace.

        Profiles are the durable home for bounded aggregate state such as
        session and agent long-term summaries.  Callers must open the store
        before use; missing rows are represented by ``None`` rather than an
        inferred legacy file.
        """
        row = await _fetchone(
            self.connection,
            "SELECT metadata_json FROM memory_profiles WHERE scope=? AND namespace=?",
            (scope, namespace),
        )
        if row is None:
            return None
        value = json.loads(str(row[0]))
        if not isinstance(value, dict):
            raise StateSchemaError("memory profile metadata must be an object")
        return value

    async def save_memory_profile(
        self,
        scope: str,
        namespace: str,
        metadata: dict[str, Any],
    ) -> None:
        """Atomically replace one current-version memory profile."""
        async with self.transaction() as connection:
            await connection.execute(
                """INSERT INTO memory_profiles(scope, namespace, metadata_json)
                   VALUES (?, ?, ?)
                   ON CONFLICT(scope, namespace) DO UPDATE SET
                     metadata_json=excluded.metadata_json""",
                (scope, namespace, _json(metadata)),
            )

    async def load_maintenance_state(self, state_key: str) -> dict[str, Any] | None:
        """Load a maintenance cursor owned by one named background process."""
        row = await _fetchone(
            self.connection,
            "SELECT value_json FROM maintenance_state WHERE state_key=?",
            (state_key,),
        )
        if row is None:
            return None
        value = json.loads(str(row[0]))
        if not isinstance(value, dict):
            raise StateSchemaError("maintenance state must be an object")
        return value

    async def save_maintenance_state(
        self,
        state_key: str,
        value: dict[str, Any],
    ) -> None:
        """Atomically replace one maintenance cursor."""
        async with self.transaction() as connection:
            await connection.execute(
                """INSERT INTO maintenance_state(state_key, value_json, updated_at_ms)
                   VALUES (?, ?, ?)
                   ON CONFLICT(state_key) DO UPDATE SET
                     value_json=excluded.value_json,
                     updated_at_ms=excluded.updated_at_ms""",
                (state_key, _json(value), _milliseconds()),
            )

    async def load_session_memory(self, scope: str) -> dict[str, Any] | None:
        """Load one session memory profile and its ordered entries."""
        profile = await _fetchone(
            self.connection,
            "SELECT metadata_json FROM memory_profiles WHERE scope=? AND namespace='memory'",
            (scope,),
        )
        if profile is None:
            return None
        metadata = json.loads(str(profile[0]))
        if not isinstance(metadata, dict):
            raise StateSchemaError("memory profile metadata must be an object")
        rows = await self.connection.execute_fetchall(
            """SELECT metadata_json FROM memory_entries
               WHERE scope=? AND namespace='memory'
               ORDER BY created_at_ms, id""",
            (scope,),
        )
        entries: list[dict[str, Any]] = []
        for row in rows:
            entry = json.loads(str(row[0]))
            if not isinstance(entry, dict):
                raise StateSchemaError("memory entry metadata must be an object")
            entries.append(entry)
        return {"session_id": scope, **metadata, "entries": entries}

    async def save_session_memory(
        self,
        scope: str,
        metadata: dict[str, Any],
        entries: Sequence[dict[str, Any]],
        *,
        max_total_entries: int,
    ) -> list[str]:
        """Atomically persist a profile, entries and FTS rows."""
        if max_total_entries <= 0:
            raise ValueError("max_total_entries must be greater than zero")
        normalized_entries = [dict(entry) for entry in entries]
        keys = [
            memory_entry_key(scope, entry, ordinal)
            for ordinal, entry in enumerate(normalized_entries)
        ]
        now_ms = _milliseconds()
        async with self.transaction() as connection:
            await connection.execute(
                """INSERT INTO memory_profiles(scope, namespace, metadata_json)
                   VALUES (?, 'memory', ?)
                   ON CONFLICT(scope, namespace) DO UPDATE SET
                     metadata_json=excluded.metadata_json""",
                (scope, _json(metadata)),
            )
            existing_rows = await connection.execute_fetchall(
                """SELECT id, entry_key FROM memory_entries
                   WHERE scope=? AND namespace='memory'""",
                (scope,),
            )
            existing = {str(row[1]): int(row[0]) for row in existing_rows}
            desired = set(keys)
            obsolete_ids = [row_id for key, row_id in existing.items() if key not in desired]
            if obsolete_ids:
                await connection.executemany(
                    "DELETE FROM memory_fts WHERE rowid=?",
                    [(row_id,) for row_id in obsolete_ids],
                )
                await connection.executemany(
                    "DELETE FROM memory_entries WHERE id=?",
                    [(row_id,) for row_id in obsolete_ids],
                )
            for key, entry in zip(keys, normalized_entries, strict=True):
                content = " ".join(
                    [
                        str(entry.get("user_snippet", "")),
                        str(entry.get("summary", "")),
                        *(str(item) for item in entry.get("facts", [])),
                    ]
                ).strip()
                created_ms = _entry_created_ms(str(entry.get("timestamp", "")))
                row_id = existing.get(key)
                if row_id is None:
                    cursor = await connection.execute(
                        """INSERT INTO memory_entries(
                               entry_key, scope, namespace, content, metadata_json,
                               created_at_ms, updated_at_ms
                           ) VALUES (?, ?, 'memory', ?, ?, ?, ?)""",
                        (key, scope, content, _json(entry), created_ms, now_ms),
                    )
                    row_id = int(cursor.lastrowid or 0)
                    await cursor.close()
                    await connection.execute(
                        "INSERT INTO memory_fts(rowid, content) VALUES (?, ?)",
                        (row_id, content),
                    )
                else:
                    await connection.execute(
                        "UPDATE memory_entries SET updated_at_ms=? WHERE id=?",
                        (now_ms, row_id),
                    )
            excess = await connection.execute_fetchall(
                """SELECT id FROM memory_entries WHERE namespace='memory'
                   ORDER BY updated_at_ms DESC, id DESC LIMIT -1 OFFSET ?""",
                (max_total_entries,),
            )
            if excess:
                excess_ids = [int(row[0]) for row in excess]
                await connection.executemany(
                    "DELETE FROM memory_fts WHERE rowid=?",
                    [(row_id,) for row_id in excess_ids],
                )
                await connection.executemany(
                    "DELETE FROM memory_entries WHERE id=?",
                    [(row_id,) for row_id in excess_ids],
                )
        return keys

    async def list_memory_entries(
        self,
        *,
        namespace: str = "memory",
    ) -> list[dict[str, Any]]:
        """Load bounded accelerator source rows for one explicit namespace."""
        rows = await self.connection.execute_fetchall(
            """SELECT entry_key, scope, metadata_json, updated_at_ms, id
               FROM memory_entries WHERE namespace=?
               ORDER BY updated_at_ms, id""",
            (namespace,),
        )
        result: list[dict[str, Any]] = []
        for row in rows:
            metadata = json.loads(str(row["metadata_json"]))
            if not isinstance(metadata, dict):
                raise StateSchemaError("memory entry metadata must be an object")
            result.append(
                {
                    "entry_key": str(row["entry_key"]),
                    "scope": str(row["scope"]),
                    "metadata": metadata,
                }
            )
        return result

    async def list_memory_embeddings(
        self,
        model: str,
    ) -> list[tuple[str, array[float], str, float]]:
        """Load vectors for one model namespace for accelerator hydration."""
        rows = await self.connection.execute_fetchall(
            """SELECT e.entry_key, v.vector_blob, v.text_hash, v.norm
               FROM memory_embeddings v
               JOIN memory_entries e ON e.id=v.memory_id
               WHERE v.model=? ORDER BY e.updated_at_ms, e.id""",
            (model,),
        )
        result: list[tuple[str, array[float], str, float]] = []
        for row in rows:
            vector = array("d")
            vector.frombytes(bytes(row["vector_blob"]))
            result.append(
                (str(row["entry_key"]), vector, str(row["text_hash"]), float(row["norm"]))
            )
        return result

    async def put_memory_embedding_by_key(
        self,
        entry_key: str,
        model: str,
        embedding: Sequence[float],
        text_hash: str,
    ) -> None:
        """Persist one finite vector for an existing durable memory entry."""
        values = array("d", (float(value) for value in embedding))
        if not values or any(not math.isfinite(value) for value in values):
            raise StateSchemaError("embedding must contain finite values")
        norm = math.sqrt(sum(value * value for value in values))
        if norm <= 0 or not math.isfinite(norm):
            raise StateSchemaError("embedding norm must be positive")
        async with self.transaction() as connection:
            row = await _fetchone(
                connection,
                "SELECT id FROM memory_entries WHERE entry_key=?",
                (entry_key,),
            )
            if row is None:
                raise KeyError(entry_key)
            dimensions = {
                int(item[0])
                for item in await connection.execute_fetchall(
                    "SELECT DISTINCT dimension FROM memory_embeddings WHERE model=?",
                    (model,),
                )
            }
            if dimensions and dimensions != {len(values)}:
                raise StateSchemaError(
                    f"embedding dimension mismatch for {model}: "
                    f"expected {sorted(dimensions)}, got {len(values)}"
                )
            await connection.execute(
                """INSERT INTO memory_embeddings VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(memory_id, model) DO UPDATE SET
                     text_hash=excluded.text_hash,
                     dimension=excluded.dimension,
                     vector_blob=excluded.vector_blob,
                     norm=excluded.norm""",
                (int(row[0]), model, text_hash, len(values), values.tobytes(), norm),
            )

    async def remove_memory_embeddings(
        self,
        entry_keys: Sequence[str],
        model: str,
    ) -> int:
        """Delete model vectors for the supplied durable entry keys."""
        if not entry_keys:
            return 0
        removed = 0
        async with self.transaction() as connection:
            for entry_key in entry_keys:
                cursor = await connection.execute(
                    """DELETE FROM memory_embeddings
                       WHERE model=? AND memory_id=(
                         SELECT id FROM memory_entries WHERE entry_key=?
                       )""",
                    (model, entry_key),
                )
                removed += max(0, cursor.rowcount)
                await cursor.close()
        return removed

    async def delete_session_memory(self, scope: str) -> list[str]:
        """Delete one session's durable entries, FTS rows, and base profile."""
        async with self.transaction() as connection:
            rows = await connection.execute_fetchall(
                """SELECT id, entry_key FROM memory_entries
                   WHERE scope=? AND namespace='memory'""",
                (scope,),
            )
            if rows:
                await connection.executemany(
                    "DELETE FROM memory_fts WHERE rowid=?",
                    [(int(row[0]),) for row in rows],
                )
            await connection.execute(
                "DELETE FROM memory_profiles WHERE scope=? AND namespace='memory'",
                (scope,),
            )
        return [str(row[1]) for row in rows]

    async def add_memory(self, scope: str, namespace: str, content: str, *, metadata: Any | None = None) -> int:
        """Insert one durable text memory and its FTS row atomically."""
        from uuid import uuid4

        now = _milliseconds()
        async with self.transaction() as connection:
            await connection.execute(
                "INSERT OR IGNORE INTO memory_profiles VALUES (?, ?, '{}')", (scope, namespace)
            )
            cursor = await connection.execute(
                """INSERT INTO memory_entries(entry_key, scope, namespace, content, metadata_json, created_at_ms, updated_at_ms)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (uuid4().hex, scope, namespace, content, _json(metadata or {}), now, now),
            )
            memory_id = int(cursor.lastrowid or 0)
            await cursor.close()
            await connection.execute(
                "INSERT INTO memory_fts(rowid, content) VALUES (?, ?)", (memory_id, content)
            )
        return memory_id

    async def put_memory_embedding(self, memory_id: int, model: str, embedding: Sequence[float]) -> None:
        """Validate and upsert a vector for one durable memory row."""
        values = [float(value) for value in embedding]
        if not values or any(not math.isfinite(value) for value in values):
            raise StateSchemaError("embedding must contain finite values")
        norm = math.sqrt(sum(value * value for value in values))
        if not math.isfinite(norm) or norm <= 0:
            raise StateSchemaError("embedding norm must be positive")
        packed = array("d", values).tobytes()
        async with self.transaction() as connection:
            rows = await connection.execute_fetchall(
                "SELECT DISTINCT dimension FROM memory_embeddings WHERE model=?",
                (model,),
            )
            dimensions = {int(row[0]) for row in rows}
            if dimensions and dimensions != {len(values)}:
                expected = sorted(dimensions)
                raise StateSchemaError(
                    f"embedding dimension mismatch for {model}: "
                    f"expected {expected}, got {len(values)}"
                )
            await connection.execute(
                """INSERT INTO memory_embeddings VALUES (?, ?, '', ?, ?, ?)
                   ON CONFLICT(memory_id, model) DO UPDATE SET
                     dimension=excluded.dimension, vector_blob=excluded.vector_blob, norm=excluded.norm""",
                (memory_id, model, len(values), packed, norm),
            )

    async def search_memory_fts(self, query: str, *, scope: str, namespace: str, limit: int = 20) -> list[dict[str, Any]]:
        """Search one memory namespace with FTS5 trigram ranking."""
        rows = await self.connection.execute_fetchall(
            """SELECT e.* FROM memory_fts f
               JOIN memory_entries e ON e.id=f.rowid
               WHERE memory_fts MATCH ? AND e.scope=? AND e.namespace=?
               ORDER BY bm25(memory_fts), e.id LIMIT ?""",
            (query, scope, namespace, max(0, int(limit))),
        )
        return [dict(row) for row in rows]

    async def search_memory_vector(self, query: Sequence[float], *, model: str, scope: str, namespace: str, limit: int = 20) -> list[dict[str, Any]]:
        """Return cosine-ranked rows for one model, scope, and namespace."""
        values = [float(value) for value in query]
        if not values or any(not math.isfinite(value) for value in values):
            raise StateSchemaError("query embedding must contain finite values")
        norm = math.sqrt(sum(value * value for value in values))
        if norm <= 0:
            raise StateSchemaError("query embedding norm must be positive")
        rows = await self.connection.execute_fetchall(
            """SELECT e.*, v.dimension, v.vector_blob, v.norm FROM memory_embeddings v
               JOIN memory_entries e ON e.id=v.memory_id
               WHERE v.model=? AND e.scope=? AND e.namespace=?""",
            (model, scope, namespace),
        )
        scored: list[tuple[float, dict[str, Any]]] = []
        for row in rows:
            if int(row["dimension"]) != len(values):
                raise StateSchemaError("embedding dimension mismatch")
            stored = array("d")
            stored.frombytes(bytes(row["vector_blob"]))
            score = sum(left * right for left, right in zip(values, stored, strict=True)) / (norm * float(row["norm"]))
            item = dict(row)
            item.pop("vector_blob", None)
            item["score"] = score
            scored.append((score, item))
        scored.sort(key=lambda item: (-item[0], int(item[1]["id"])))
        return [item for _, item in scored[: max(0, int(limit))]]

    async def acquire_lease(self, resource: str, owner: str, *, now: float, ttl: float) -> int:
        """Acquire or take over an expired lease and return its generation."""
        now_ms, expires = _milliseconds(now), _milliseconds(now + ttl)
        async with self.transaction() as connection:
            row = await _fetchone(connection, "SELECT owner, expires_at_ms, generation FROM process_leases WHERE resource=?", (resource,))
            if row is not None and str(row[0]) != owner and int(row[1]) > now_ms:
                raise StateConflictError(f"lease is owned by {row[0]}")
            generation = (int(row[2]) + 1) if row is not None else 1
            await connection.execute(
                """INSERT INTO process_leases VALUES (?, ?, ?, ?)
                   ON CONFLICT(resource) DO UPDATE SET owner=excluded.owner,
                     expires_at_ms=excluded.expires_at_ms, generation=excluded.generation""",
                (resource, owner, expires, generation),
            )
        return generation

    async def renew_lease(self, resource: str, owner: str, *, now: float, ttl: float = 20.0) -> None:
        """Extend a lease only while ownership remains unchanged."""
        async with self.transaction() as connection:
            row = await _fetchone(connection, "SELECT owner FROM process_leases WHERE resource=?", (resource,))
            if row is None or str(row[0]) != owner:
                current = str(row[0]) if row is not None else "nobody"
                raise StateConflictError(f"lease is owned by {current}")
            await connection.execute(
                "UPDATE process_leases SET expires_at_ms=? WHERE resource=?", (_milliseconds(now + ttl), resource)
            )

    async def release_lease(self, resource: str, owner: str) -> bool:
        """Release a lease only when it is still owned by the caller."""
        cursor = await self.connection.execute(
            "DELETE FROM process_leases WHERE resource=? AND owner=?",
            (resource, owner),
        )
        changed = cursor.rowcount
        await cursor.close()
        return changed == 1

    async def claim_feishu_message(self, message_id: str, owner: str, *, now: float, lease_seconds: float = 300.0) -> bool:
        """Claim one inbound event exactly once until expiry or completion."""
        now_ms = _milliseconds(now)
        async with self.transaction() as connection:
            row = await _fetchone(connection, "SELECT owner, claim_until_ms, completed_at_ms FROM feishu_message_claims WHERE message_id=?", (message_id,))
            if row is not None:
                if row[2] is not None:
                    return False
                if row[1] is not None and int(row[1]) > now_ms:
                    return False
            await connection.execute(
                """INSERT INTO feishu_message_claims VALUES (?, ?, ?, NULL)
                   ON CONFLICT(message_id) DO UPDATE SET owner=excluded.owner,
                     claim_until_ms=excluded.claim_until_ms, completed_at_ms=NULL""",
                (message_id, owner, _milliseconds(now + lease_seconds)),
            )
        return True

    async def complete_feishu_message(self, message_id: str, owner: str) -> None:
        """Mark an owned Feishu claim complete or raise on lost ownership."""
        cursor = await self.connection.execute(
            """UPDATE feishu_message_claims SET claim_until_ms=NULL, completed_at_ms=?
               WHERE message_id=? AND owner=? AND completed_at_ms IS NULL""",
            (_milliseconds(), message_id, owner),
        )
        changed = cursor.rowcount
        await cursor.close()
        if changed != 1:
            raise StateConflictError("feishu message claim is not owned by caller")

    async def upsert_knowledge_mount(self, name: str, source_path: str) -> int:
        """Upsert one filesystem knowledge mount and return its row id."""
        await self.connection.execute(
            """INSERT INTO knowledge_mounts(name, source_path, updated_at_ms) VALUES (?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET source_path=excluded.source_path,
                 updated_at_ms=excluded.updated_at_ms""",
            (name, source_path, _milliseconds()),
        )
        row = await _fetchone(self.connection, "SELECT id FROM knowledge_mounts WHERE name=?", (name,))
        assert row is not None
        return int(row[0])

    async def upsert_knowledge_document(
        self,
        mount_id: int,
        relative_path: str,
        title: str,
        content: str,
        content_hash: str,
        metadata: Any | None = None,
    ) -> int:
        """Commit document metadata, content, and its FTS row atomically."""
        async with self.transaction() as connection:
            row = await _fetchone(connection, "SELECT id FROM knowledge_documents WHERE mount_id=? AND relative_path=?", (mount_id, relative_path))
            if row is None:
                cursor = await connection.execute(
                    """INSERT INTO knowledge_documents(
                           mount_id, relative_path, title, content, content_hash,
                           metadata_json, updated_at_ms
                       ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        mount_id,
                        relative_path,
                        title,
                        content,
                        content_hash,
                        _json(metadata or {}),
                        _milliseconds(),
                    ),
                )
                document_id = int(cursor.lastrowid or 0)
                await cursor.close()
            else:
                document_id = int(row[0])
                await connection.execute(
                    """UPDATE knowledge_documents SET title=?, content=?, content_hash=?,
                         metadata_json=?, updated_at_ms=?
                       WHERE id=?""",
                    (
                        title,
                        content,
                        content_hash,
                        _json(metadata or {}),
                        _milliseconds(),
                        document_id,
                    ),
                )
                await connection.execute("DELETE FROM knowledge_fts WHERE rowid=?", (document_id,))
            await connection.execute(
                "INSERT INTO knowledge_fts(rowid, title, content) VALUES (?, ?, ?)",
                (document_id, title, content),
            )
        return document_id

    async def search_knowledge(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
        """Search indexed knowledge documents with FTS5 ranking."""
        rows = await self.connection.execute_fetchall(
            """SELECT d.* FROM knowledge_fts f JOIN knowledge_documents d ON d.id=f.rowid
               WHERE knowledge_fts MATCH ? ORDER BY bm25(knowledge_fts), d.id LIMIT ?""",
            (query, max(0, int(limit))),
        )
        return [dict(row) for row in rows]


__all__ = [
    "STATE_SCHEMA_VERSION",
    "StateConflictError",
    "StateError",
    "StateSchemaError",
    "StateStore",
    "memory_entry_key",
]
