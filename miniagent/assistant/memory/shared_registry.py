"""SQLite-backed shared memory-entry registry."""

from __future__ import annotations

import json
import math
import time
from array import array
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from miniagent.agent.types.memory import MemoryEntry, MemoryEntryInput
from miniagent.assistant.infrastructure.json_config import get_config
from miniagent.assistant.state.sync import immediate_transaction, open_state_database


@dataclass
class SharedEntry:
    """Shared text payload referenced by keyword and embedding search."""

    session_id: str
    timestamp: str
    user_snippet: str
    summary: str
    facts: list[str] = field(default_factory=list)


class MemoryEntryRegistry:
    """Store each durable memory payload once in the project database."""

    def __init__(self, state_dir: str = "workspaces") -> None:
        self._state_dir = state_dir
        self._max_entries = int(get_config("memory.registry_max_entries", 3000))

    @staticmethod
    def _make_key(session_id: str, timestamp: str) -> str:
        return f"{session_id}:{timestamp}"

    @staticmethod
    def _metadata(entry: SharedEntry) -> str:
        return json.dumps(
            {
                "timestamp": entry.timestamp,
                "user_snippet": entry.user_snippet,
                "summary": entry.summary,
                "facts": entry.facts,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @staticmethod
    def _from_row(row: Any) -> SharedEntry:
        data = json.loads(str(row["metadata_json"]))
        return SharedEntry(
            session_id=str(row["scope"]),
            timestamp=str(data.get("timestamp", "")),
            user_snippet=str(data.get("user_snippet", "")),
            summary=str(data.get("summary", "")),
            facts=[str(item) for item in data.get("facts", [])],
        )

    def register(
        self,
        session_id: str,
        entry: MemoryEntryInput | MemoryEntry,
    ) -> str:
        key = self._make_key(session_id, entry.timestamp)
        shared = SharedEntry(
            session_id=session_id,
            timestamp=entry.timestamp,
            user_snippet=entry.user_snippet,
            summary=entry.summary,
            facts=list(getattr(entry, "facts", []) or []),
        )
        content = " ".join(
            [shared.user_snippet, shared.summary, *shared.facts]
        ).strip()
        try:
            created_ms = int(datetime.fromisoformat(entry.timestamp.replace("Z", "+00:00")).timestamp() * 1000)
        except (TypeError, ValueError):
            created_ms = int(time.time() * 1000)
        now_ms = int(time.time() * 1000)
        with open_state_database(self._state_dir) as connection:
            with immediate_transaction(connection):
                connection.execute(
                    "INSERT OR IGNORE INTO memory_profiles VALUES (?, 'memory', '{}')",
                    (session_id,),
                )
                row = connection.execute(
                    "SELECT id, content FROM memory_entries WHERE entry_key=?", (key,)
                ).fetchone()
                if row is None:
                    cursor = connection.execute(
                        """INSERT INTO memory_entries(
                               entry_key, scope, namespace, content, metadata_json,
                               created_at_ms, updated_at_ms
                           ) VALUES (?, ?, 'memory', ?, ?, ?, ?)""",
                        (key, session_id, content, self._metadata(shared), created_ms, now_ms),
                    )
                    if cursor.lastrowid is None:  # pragma: no cover - SQLite contract
                        raise RuntimeError("memory insert did not return a row id")
                    memory_id = int(cursor.lastrowid)
                else:
                    memory_id = int(row[0])
                    if str(row[1]) != content:
                        connection.execute(
                            "DELETE FROM memory_embeddings WHERE memory_id=?", (memory_id,)
                        )
                    connection.execute(
                        """UPDATE memory_entries SET content=?, metadata_json=?, updated_at_ms=?
                           WHERE id=?""",
                        (content, self._metadata(shared), now_ms, memory_id),
                    )
                    connection.execute("DELETE FROM memory_fts WHERE rowid=?", (memory_id,))
                connection.execute(
                    "INSERT INTO memory_fts(rowid, content) VALUES (?, ?)",
                    (memory_id, content),
                )
                excess = connection.execute(
                    """SELECT id FROM memory_entries WHERE namespace='memory'
                       ORDER BY updated_at_ms DESC, id DESC LIMIT -1 OFFSET ?""",
                    (self._max_entries,),
                ).fetchall()
                if excess:
                    ids = [int(item[0]) for item in excess]
                    connection.executemany(
                        "DELETE FROM memory_fts WHERE rowid=?", [(item,) for item in ids]
                    )
                    connection.executemany(
                        "DELETE FROM memory_entries WHERE id=?", [(item,) for item in ids]
                    )
        return key

    def get(self, key: str) -> SharedEntry | None:
        with open_state_database(self._state_dir) as connection:
            row = connection.execute(
                """SELECT scope, metadata_json FROM memory_entries
                   WHERE entry_key=? AND namespace='memory'""",
                (key,),
            ).fetchone()
        return self._from_row(row) if row is not None else None

    def contains(self, key: str) -> bool:
        return self.get(key) is not None

    def evict(self, key: str) -> bool:
        with open_state_database(self._state_dir) as connection:
            with immediate_transaction(connection):
                row = connection.execute(
                    "SELECT id FROM memory_entries WHERE entry_key=?", (key,)
                ).fetchone()
                if row is None:
                    return False
                connection.execute("DELETE FROM memory_fts WHERE rowid=?", (int(row[0]),))
                connection.execute("DELETE FROM memory_entries WHERE id=?", (int(row[0]),))
                return True

    def remove_session_entries(self, session_id: str) -> list[str]:
        with open_state_database(self._state_dir) as connection:
            with immediate_transaction(connection):
                rows = connection.execute(
                    """SELECT id, entry_key FROM memory_entries
                       WHERE scope=? AND namespace='memory'""",
                    (session_id,),
                ).fetchall()
                connection.executemany(
                    "DELETE FROM memory_fts WHERE rowid=?",
                    [(int(row[0]),) for row in rows],
                )
                connection.execute(
                    "DELETE FROM memory_entries WHERE scope=? AND namespace='memory'",
                    (session_id,),
                )
        return [str(row[1]) for row in rows]

    def all_entries(self) -> list[tuple[str, SharedEntry]]:
        with open_state_database(self._state_dir) as connection:
            rows = connection.execute(
                """SELECT entry_key, scope, metadata_json FROM memory_entries
                   WHERE namespace='memory' ORDER BY updated_at_ms, id"""
            ).fetchall()
        return [(str(row[0]), self._from_row(row)) for row in rows]

    def put_embedding(
        self,
        entry_key: str,
        model: str,
        embedding: Sequence[float],
        text_hash: str,
    ) -> None:
        """Store one finite vector for an existing memory entry."""
        values = array("d", (float(value) for value in embedding))
        if not values or any(not math.isfinite(value) for value in values):
            raise ValueError("embedding must contain finite values")
        norm = math.sqrt(sum(value * value for value in values))
        if norm <= 0 or not math.isfinite(norm):
            raise ValueError("embedding norm must be positive")
        with open_state_database(self._state_dir) as connection:
            with immediate_transaction(connection):
                row = connection.execute(
                    "SELECT id FROM memory_entries WHERE entry_key=?", (entry_key,)
                ).fetchone()
                if row is None:
                    raise KeyError(entry_key)
                dimensions = {
                    int(item[0])
                    for item in connection.execute(
                        "SELECT DISTINCT dimension FROM memory_embeddings WHERE model=?",
                        (model,),
                    )
                }
                if dimensions and dimensions != {len(values)}:
                    expected = sorted(dimensions)
                    raise ValueError(
                        f"embedding dimension mismatch for {model}: "
                        f"expected {expected}, got {len(values)}"
                    )
                connection.execute(
                    """INSERT INTO memory_embeddings VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(memory_id, model) DO UPDATE SET
                         text_hash=excluded.text_hash,
                         dimension=excluded.dimension,
                         vector_blob=excluded.vector_blob,
                         norm=excluded.norm""",
                    (int(row[0]), model, text_hash, len(values), values.tobytes(), norm),
                )

    def list_embeddings(
        self, model: str
    ) -> list[tuple[str, array[float], str, float]]:
        """Load vectors for one explicit model namespace."""
        with open_state_database(self._state_dir) as connection:
            rows = connection.execute(
                """SELECT e.entry_key, v.vector_blob, v.text_hash, v.norm
                   FROM memory_embeddings v
                   JOIN memory_entries e ON e.id=v.memory_id
                   WHERE v.model=? ORDER BY e.updated_at_ms, e.id""",
                (model,),
            ).fetchall()
        result: list[tuple[str, array[float], str, float]] = []
        for row in rows:
            vector = array("d")
            vector.frombytes(bytes(row[1]))
            result.append((str(row[0]), vector, str(row[2]), float(row[3])))
        return result

    def remove_embeddings(self, entry_keys: list[str], model: str) -> int:
        if not entry_keys:
            return 0
        with open_state_database(self._state_dir) as connection:
            with immediate_transaction(connection):
                removed = 0
                for entry_key in entry_keys:
                    cursor = connection.execute(
                        """DELETE FROM memory_embeddings
                           WHERE model=? AND memory_id=(
                             SELECT id FROM memory_entries WHERE entry_key=?
                           )""",
                        (model, entry_key),
                    )
                    removed += max(0, cursor.rowcount)
                return removed

    def get_stats(self) -> dict[str, Any]:
        with open_state_database(self._state_dir) as connection:
            count = int(
                connection.execute(
                    "SELECT count(*) FROM memory_entries WHERE namespace='memory'"
                ).fetchone()[0]
            )
        return {"total_entries": count}

    def save(self) -> None:
        """Writes are committed by each mutation; retained as an explicit flush point."""

    def clear(self) -> None:
        with open_state_database(self._state_dir) as connection:
            with immediate_transaction(connection):
                ids = connection.execute(
                    "SELECT id FROM memory_entries WHERE namespace='memory'"
                ).fetchall()
                connection.executemany(
                    "DELETE FROM memory_fts WHERE rowid=?", [(int(row[0]),) for row in ids]
                )
                connection.execute("DELETE FROM memory_entries WHERE namespace='memory'")


__all__ = ["MemoryEntryRegistry", "SharedEntry"]
