"""Transactional Feishu inbound-message deduplication."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from miniagent.agent.constants import DEDUP_FLUSH_INTERVAL, DEDUP_FLUSH_THRESHOLD
from miniagent.assistant.infrastructure.paths import resolve_state_dir
from miniagent.assistant.state.sync import immediate_transaction, open_state_database

DEDUP_TTL_MS = 5 * 60 * 1000
DEDUP_MAX_SIZE = 2000


class FeishuDeduplicator:
    """Claim inbound messages across processes using the project database."""

    def __init__(self, state_dir: str | None = None) -> None:
        self._state_dir = Path(state_dir or resolve_state_dir())
        self._owner = f"feishu:{uuid4().hex}"

    @staticmethod
    def _key(message_id: str) -> str:
        value = message_id.strip()
        return f"mini-agent:{value}" if value else ""

    def try_begin_processing(self, message_id: str) -> bool:
        """Claim a message unless another live claim or completion exists."""
        key = self._key(message_id)
        if not key:
            return True
        now_ms = int(time.time() * 1000)
        cutoff_ms = now_ms - DEDUP_TTL_MS
        with open_state_database(self._state_dir) as connection:
            with immediate_transaction(connection):
                connection.execute(
                    "DELETE FROM feishu_message_claims WHERE completed_at_ms < ?",
                    (cutoff_ms,),
                )
                row = connection.execute(
                    """SELECT owner, claim_until_ms, completed_at_ms
                       FROM feishu_message_claims WHERE message_id=?""",
                    (key,),
                ).fetchone()
                if row is not None:
                    if row[2] is not None:
                        return False
                    if row[1] is not None and int(row[1]) > now_ms:
                        return False
                connection.execute(
                    """INSERT INTO feishu_message_claims VALUES (?, ?, ?, NULL)
                       ON CONFLICT(message_id) DO UPDATE SET owner=excluded.owner,
                         claim_until_ms=excluded.claim_until_ms, completed_at_ms=NULL""",
                    (key, self._owner, now_ms + DEDUP_TTL_MS),
                )
                return True

    def release_processing(self, message_id: str) -> None:
        """Complete a claim owned by this deduplicator."""
        key = self._key(message_id)
        if not key:
            return
        with open_state_database(self._state_dir) as connection:
            with immediate_transaction(connection):
                connection.execute(
                    """UPDATE feishu_message_claims
                       SET claim_until_ms=NULL, completed_at_ms=?
                       WHERE message_id=? AND owner=? AND completed_at_ms IS NULL""",
                    (int(time.time() * 1000), key, self._owner),
                )
                connection.execute(
                    """DELETE FROM feishu_message_claims WHERE message_id IN (
                           SELECT message_id FROM feishu_message_claims
                           WHERE completed_at_ms IS NOT NULL
                           ORDER BY completed_at_ms DESC, message_id DESC
                           LIMIT -1 OFFSET ?
                       )""",
                    (max(0, DEDUP_MAX_SIZE),),
                )

    def abandon_processing_claim(self, message_id: str) -> None:
        """Release an unfinished claim owned by this deduplicator."""
        key = self._key(message_id)
        if not key:
            return
        with open_state_database(self._state_dir) as connection:
            connection.execute(
                """DELETE FROM feishu_message_claims
                   WHERE message_id=? AND owner=? AND completed_at_ms IS NULL""",
                (key, self._owner),
            )

    def stats(self) -> dict[str, Any]:
        """Return current durable claim counts."""
        now_ms = int(time.time() * 1000)
        with open_state_database(self._state_dir) as connection:
            processing = int(
                connection.execute(
                    """SELECT count(*) FROM feishu_message_claims
                       WHERE completed_at_ms IS NULL AND claim_until_ms > ?""",
                    (now_ms,),
                ).fetchone()[0]
            )
            processed = int(
                connection.execute(
                    "SELECT count(*) FROM feishu_message_claims WHERE completed_at_ms IS NOT NULL"
                ).fetchone()[0]
            )
        return {
            "processing_claims": processing,
            "disk_dedup": processed,
            "dirty": False,
            "state_dir": str(self._state_dir),
        }

    async def flush(self) -> None:
        """Prune expired completions; writes are already committed per operation."""
        cutoff_ms = int(time.time() * 1000) - DEDUP_TTL_MS
        with open_state_database(self._state_dir) as connection:
            connection.execute(
                "DELETE FROM feishu_message_claims WHERE completed_at_ms < ?",
                (cutoff_ms,),
            )

    async def close(self) -> None:
        """Release unfinished claims owned by this process."""
        with open_state_database(self._state_dir) as connection:
            connection.execute(
                """DELETE FROM feishu_message_claims
                   WHERE owner=? AND completed_at_ms IS NULL""",
                (self._owner,),
            )
        await self.flush()


__all__ = [
    "DEDUP_FLUSH_INTERVAL",
    "DEDUP_FLUSH_THRESHOLD",
    "DEDUP_MAX_SIZE",
    "DEDUP_TTL_MS",
    "FeishuDeduplicator",
]
