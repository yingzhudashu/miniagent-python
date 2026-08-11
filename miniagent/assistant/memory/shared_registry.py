"""Bounded in-process cache shared by memory search accelerators.

SQLite is the sole durable source.  This registry is hydrated once during
``MemoryRuntime.start`` and is mutated only after durable transactions commit,
so keyword and vector search can expand results without opening a database for
every candidate.
"""

from __future__ import annotations

import math
import threading
from array import array
from collections import OrderedDict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from miniagent.agent.types.memory import MemoryEntry, MemoryEntryInput
from miniagent.assistant.infrastructure.json_config import get_config


@dataclass
class SharedEntry:
    """Shared text payload referenced by keyword and embedding search."""

    session_id: str
    timestamp: str
    user_snippet: str
    summary: str
    facts: list[str] = field(default_factory=list)


class MemoryEntryRegistry:
    """Thread-safe bounded cache of durable memory entries and vectors."""

    def __init__(self, *, max_entries: int | None = None) -> None:
        configured = int(get_config("memory.registry_max_entries", 3000))
        self._max_entries = max(1, configured if max_entries is None else max_entries)
        self._entries: OrderedDict[str, SharedEntry] = OrderedDict()
        self._embeddings: dict[
            str,
            OrderedDict[str, tuple[array[float], str, float]],
        ] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _make_key(session_id: str, timestamp: str) -> str:
        """Build the fallback key used by direct accelerator callers."""
        return f"{session_id}:{timestamp}"

    @staticmethod
    def _shared(
        session_id: str,
        entry: MemoryEntryInput | MemoryEntry | dict[str, Any],
    ) -> SharedEntry:
        """Normalize a current durable entry payload for cache storage."""
        if isinstance(entry, dict):
            timestamp = str(entry.get("timestamp", ""))
            user_snippet = str(entry.get("user_snippet", ""))
            summary = str(entry.get("summary", ""))
            facts = [str(item) for item in entry.get("facts", [])]
        else:
            timestamp = entry.timestamp
            user_snippet = entry.user_snippet
            summary = entry.summary
            facts = [str(item) for item in (getattr(entry, "facts", []) or [])]
        return SharedEntry(session_id, timestamp, user_snippet, summary, facts)

    def _evict_excess(self) -> None:
        """Apply the shared bound and remove vectors for evicted text rows."""
        while len(self._entries) > self._max_entries:
            key, _ = self._entries.popitem(last=False)
            for model_entries in self._embeddings.values():
                model_entries.pop(key, None)

    def hydrate_entries(self, rows: Iterable[dict[str, Any]]) -> None:
        """Replace cache contents from ordered rows loaded by ``StateStore``."""
        with self._lock:
            self._entries.clear()
            for row in rows:
                key = str(row["entry_key"])
                self._entries[key] = self._shared(
                    str(row["scope"]),
                    row["metadata"],
                )
            self._evict_excess()

    def hydrate_embeddings(
        self,
        model: str,
        rows: Iterable[tuple[str, array[float], str, float]],
    ) -> None:
        """Replace one model cache with vectors whose text rows still exist."""
        with self._lock:
            loaded: OrderedDict[str, tuple[array[float], str, float]] = OrderedDict()
            for key, vector, text_hash, norm in rows:
                if key in self._entries:
                    loaded[key] = (array("d", vector), text_hash, float(norm))
            self._embeddings[model] = loaded

    def register(
        self,
        session_id: str,
        entry: MemoryEntryInput | MemoryEntry,
        *,
        entry_key: str | None = None,
    ) -> str:
        """Cache one committed text entry and invalidate changed vectors."""
        key = entry_key or self._make_key(session_id, entry.timestamp)
        shared = self._shared(session_id, entry)
        with self._lock:
            previous = self._entries.get(key)
            if previous is not None and previous != shared:
                for model_entries in self._embeddings.values():
                    model_entries.pop(key, None)
            self._entries[key] = shared
            self._entries.move_to_end(key)
            self._evict_excess()
        return key

    def get(self, key: str) -> SharedEntry | None:
        """Return one cached entry without changing its durability ordering."""
        with self._lock:
            return self._entries.get(key)

    def contains(self, key: str) -> bool:
        """Return whether an entry is available for result expansion."""
        with self._lock:
            return key in self._entries

    def evict(self, key: str) -> bool:
        """Evict one derived entry and every model vector referencing it."""
        with self._lock:
            removed = self._entries.pop(key, None) is not None
            if removed:
                for model_entries in self._embeddings.values():
                    model_entries.pop(key, None)
            return removed

    def remove_session_entries(self, session_id: str) -> list[str]:
        """Evict and return every key belonging to one session."""
        with self._lock:
            keys = [
                key for key, entry in self._entries.items() if entry.session_id == session_id
            ]
            for key in keys:
                self._entries.pop(key, None)
                for model_entries in self._embeddings.values():
                    model_entries.pop(key, None)
            return keys

    def all_entries(self) -> list[tuple[str, SharedEntry]]:
        """Return a stable snapshot for rebuilding a derived keyword index."""
        with self._lock:
            return list(self._entries.items())

    def put_embedding(
        self,
        entry_key: str,
        model: str,
        embedding: Sequence[float],
        text_hash: str,
    ) -> None:
        """Cache one committed finite vector in an explicit model namespace."""
        values = array("d", (float(value) for value in embedding))
        if not values or any(not math.isfinite(value) for value in values):
            raise ValueError("embedding must contain finite values")
        norm = math.sqrt(sum(value * value for value in values))
        if norm <= 0 or not math.isfinite(norm):
            raise ValueError("embedding norm must be positive")
        with self._lock:
            if entry_key not in self._entries:
                raise KeyError(entry_key)
            model_entries = self._embeddings.setdefault(model, OrderedDict())
            dimensions = {len(item[0]) for item in model_entries.values()}
            if dimensions and dimensions != {len(values)}:
                raise ValueError(
                    f"embedding dimension mismatch for {model}: "
                    f"expected {sorted(dimensions)}, got {len(values)}"
                )
            model_entries[entry_key] = (values, text_hash, norm)
            model_entries.move_to_end(entry_key)

    def list_embeddings(
        self,
        model: str,
    ) -> list[tuple[str, array[float], str, float]]:
        """Return a copy-safe snapshot of one model's cached vectors."""
        with self._lock:
            return [
                (key, array("d", vector), text_hash, norm)
                for key, (vector, text_hash, norm) in self._embeddings.get(
                    model, OrderedDict()
                ).items()
            ]

    def remove_embeddings(self, entry_keys: Sequence[str], model: str) -> int:
        """Evict model vectors without altering their durable text rows."""
        with self._lock:
            model_entries = self._embeddings.get(model)
            if model_entries is None:
                return 0
            removed = 0
            for key in entry_keys:
                removed += model_entries.pop(key, None) is not None
            return removed

    def get_stats(self) -> dict[str, Any]:
        """Return bounded cache cardinality for diagnostics."""
        with self._lock:
            return {"total_entries": len(self._entries)}

    def save(self) -> None:
        """Retain an explicit no-op flush point for lifecycle symmetry."""

    def clear(self) -> None:
        """Clear derived entries and vectors without mutating SQLite."""
        with self._lock:
            self._entries.clear()
            self._embeddings.clear()


__all__ = ["MemoryEntryRegistry", "SharedEntry"]
