"""Instance-owned Feishu message deduplication with explicit persistence."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from miniagent.core.constants import DEDUP_FLUSH_INTERVAL, DEDUP_FLUSH_THRESHOLD
from miniagent.infrastructure.atomic_json import atomic_dump_json
from miniagent.infrastructure.logger import get_logger
from miniagent.infrastructure.paths import resolve_state_dir

_logger = get_logger(__name__)

DEDUP_TTL_MS = 5 * 60 * 1000
DEDUP_MAX_SIZE = 2000


class FeishuDeduplicator:
    """Claim inbound messages and persist recently completed message IDs."""

    def __init__(self, state_dir: str | None = None) -> None:
        root = Path(state_dir or resolve_state_dir()) / "feishu" / "dedup"
        self._state_dir = root
        self._dedup_file = root / "processed.json"
        self._processing_claims: dict[str, float] = {}
        self._processed: dict[str, float] = {}
        self._dirty = False
        self._generation = 0
        self._last_flush_time = 0.0
        self._flush_task: asyncio.Task[None] | None = None
        self._flush_lock = asyncio.Lock()
        self._load()

    @staticmethod
    def _key(message_id: str) -> str:
        value = message_id.strip()
        return f"mini-agent:{value}" if value else ""

    def _load(self) -> None:
        try:
            if self._dedup_file.is_file():
                data = json.loads(self._dedup_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    loaded = {
                        str(key): float(value)
                        for key, value in data.items()
                        if isinstance(value, (int, float))
                    }
                    cutoff = time.time() - DEDUP_TTL_MS / 1000.0
                    self._processed = {
                        key: value for key, value in loaded.items() if value >= cutoff
                    }
                    if len(self._processed) != len(loaded):
                        self._dirty = True
                        self._generation += 1
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
            _logger.debug("加载飞书去重状态失败: %s", error)
            self._processed = {}

    def try_begin_processing(self, message_id: str) -> bool:
        """Claim a message unless it is active or already completed."""
        key = self._key(message_id)
        if not key:
            return True
        self._prune_if_needed()
        cutoff = time.time() - DEDUP_TTL_MS / 1000.0
        processed_at = self._processed.get(key)
        if processed_at is not None and processed_at < cutoff:
            self._processed.pop(key, None)
            self._dirty = True
            self._generation += 1
            self._maybe_schedule_flush()
        claimed_at = self._processing_claims.get(key)
        if claimed_at is not None and claimed_at < cutoff:
            self._processing_claims.pop(key, None)
        if key in self._processed or key in self._processing_claims:
            return False
        self._processing_claims[key] = time.time()
        return True

    def release_processing(self, message_id: str) -> None:
        """Complete a claim and mark the message for persistent deduplication."""
        key = self._key(message_id)
        if not key:
            return
        self._processing_claims.pop(key, None)
        self._processed[key] = time.time()
        self._dirty = True
        self._generation += 1
        if len(self._processed) > DEDUP_MAX_SIZE:
            oldest = sorted(self._processed, key=self._processed.get)
            for stale in oldest[: max(1, len(oldest) // 5)]:
                del self._processed[stale]
        self._maybe_schedule_flush()

    def abandon_processing_claim(self, message_id: str) -> None:
        """Release an in-flight claim without marking the message complete."""
        key = self._key(message_id)
        if key:
            self._processing_claims.pop(key, None)

    def stats(self) -> dict[str, Any]:
        """Return operational counts without exposing mutable dictionaries."""
        return {
            "processing_claims": len(self._processing_claims),
            "disk_dedup": len(self._processed),
            "dirty": self._dirty,
            "state_dir": str(self._state_dir),
        }

    def _prune_if_needed(self) -> None:
        threshold = int(DEDUP_MAX_SIZE * 0.8)
        if (
            len(self._processing_claims) <= threshold
            and len(self._processed) <= threshold
        ):
            return
        cutoff = time.time() - DEDUP_TTL_MS / 1000.0
        self._processing_claims = {
            key: value
            for key, value in self._processing_claims.items()
            if value >= cutoff
        }
        before = len(self._processed)
        self._processed = {
            key: value for key, value in self._processed.items() if value >= cutoff
        }
        if len(self._processed) != before:
            self._dirty = True
            self._generation += 1
            self._maybe_schedule_flush()

    def _maybe_schedule_flush(self) -> None:
        now = time.monotonic()
        due = (
            len(self._processed) >= DEDUP_FLUSH_THRESHOLD
            or now - self._last_flush_time >= DEDUP_FLUSH_INTERVAL
        )
        if not due or not self._dirty:
            return
        self._last_flush_time = now
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._flush_task is None or self._flush_task.done():
            self._flush_task = loop.create_task(self.flush())

    def _write_snapshot(self, snapshot: dict[str, float]) -> None:
        try:
            atomic_dump_json(self._dedup_file, snapshot)
        except OSError as error:
            _logger.debug("保存飞书去重状态失败: %s", error)
            raise

    async def flush(self) -> None:
        """Persist dirty state without blocking the event loop."""
        async with self._flush_lock:
            if not self._dirty:
                return
            generation = self._generation
            snapshot = dict(self._processed)
            try:
                await asyncio.to_thread(self._write_snapshot, snapshot)
            except OSError:
                return
            if self._generation == generation:
                self._dirty = False

    async def close(self) -> None:
        """Wait for any scheduled flush and persist remaining dirty state."""
        task = self._flush_task
        if task is not None and task is not asyncio.current_task():
            await asyncio.gather(task, return_exceptions=True)
        await self.flush()


__all__ = [
    "DEDUP_FLUSH_INTERVAL",
    "DEDUP_FLUSH_THRESHOLD",
    "DEDUP_MAX_SIZE",
    "DEDUP_TTL_MS",
    "FeishuDeduplicator",
]
