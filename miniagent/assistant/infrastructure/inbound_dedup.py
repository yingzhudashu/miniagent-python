"""Channel-neutral transactional inbound-message declarations."""

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


class InboundDeduplicator:
    """Claim one channel's inbound messages across processes."""

    def __init__(self, channel: str, state_dir: str | None = None) -> None:
        normalized = channel.strip().lower()
        if not normalized:
            raise ValueError("channel must not be empty")
        self.channel = normalized
        self._state_dir = Path(state_dir or resolve_state_dir())
        self._owner = f"{normalized}:{uuid4().hex}"

    def try_begin_processing(self, message_id: str) -> bool:
        """Claim a message for processing, returning false for active/completed claims."""
        message_id = message_id.strip()
        if not message_id:
            return False
        now_ms = int(time.time() * 1000)
        cutoff_ms = now_ms - DEDUP_TTL_MS
        with open_state_database(self._state_dir) as connection:
            with immediate_transaction(connection):
                connection.execute(
                    """DELETE FROM inbound_message_claims
                       WHERE channel=? AND completed_at_ms < ?""",
                    (self.channel, cutoff_ms),
                )
                row = connection.execute(
                    """SELECT owner, claim_until_ms, completed_at_ms
                       FROM inbound_message_claims WHERE channel=? AND message_id=?""",
                    (self.channel, message_id),
                ).fetchone()
                if row is not None:
                    if row[2] is not None:
                        return False
                    if row[1] is not None and int(row[1]) > now_ms:
                        return False
                connection.execute(
                    """INSERT INTO inbound_message_claims VALUES (?, ?, ?, ?, NULL)
                       ON CONFLICT(channel, message_id) DO UPDATE SET owner=excluded.owner,
                         claim_until_ms=excluded.claim_until_ms, completed_at_ms=NULL""",
                    (self.channel, message_id, self._owner, now_ms + DEDUP_TTL_MS),
                )
                return True

    def complete(self, message_id: str) -> None:
        """Complete a declaration only after successful channel delivery."""
        message_id = message_id.strip()
        if not message_id:
            return
        with open_state_database(self._state_dir) as connection:
            with immediate_transaction(connection):
                connection.execute(
                    """UPDATE inbound_message_claims
                       SET claim_until_ms=NULL, completed_at_ms=?
                       WHERE channel=? AND message_id=? AND owner=?
                         AND completed_at_ms IS NULL""",
                    (int(time.time() * 1000), self.channel, message_id, self._owner),
                )
                connection.execute(
                    """DELETE FROM inbound_message_claims WHERE rowid IN (
                           SELECT rowid FROM inbound_message_claims
                           WHERE channel=? AND completed_at_ms IS NOT NULL
                           ORDER BY completed_at_ms DESC, message_id DESC
                           LIMIT -1 OFFSET ?
                       )""",
                    (self.channel, max(0, DEDUP_MAX_SIZE)),
                )

    def abandon(self, message_id: str) -> None:
        """Release an unfinished declaration so upstream redelivery can retry."""
        message_id = message_id.strip()
        if not message_id:
            return
        with open_state_database(self._state_dir) as connection:
            connection.execute(
                """DELETE FROM inbound_message_claims
                   WHERE channel=? AND message_id=? AND owner=?
                     AND completed_at_ms IS NULL""",
                (self.channel, message_id, self._owner),
            )

    def stats(self) -> dict[str, Any]:
        """Return current processing and completed-claim counts."""
        now_ms = int(time.time() * 1000)
        with open_state_database(self._state_dir) as connection:
            processing = int(
                connection.execute(
                    """SELECT count(*) FROM inbound_message_claims
                       WHERE channel=? AND completed_at_ms IS NULL AND claim_until_ms > ?""",
                    (self.channel, now_ms),
                ).fetchone()[0]
            )
            processed = int(
                connection.execute(
                    """SELECT count(*) FROM inbound_message_claims
                       WHERE channel=? AND completed_at_ms IS NOT NULL""",
                    (self.channel,),
                ).fetchone()[0]
            )
        return {
            "processing_claims": processing,
            "disk_dedup": processed,
            "dirty": False,
            "state_dir": str(self._state_dir),
        }

    async def flush(self) -> None:
        """Remove completed claims older than the deduplication retention window."""
        cutoff_ms = int(time.time() * 1000) - DEDUP_TTL_MS
        with open_state_database(self._state_dir) as connection:
            connection.execute(
                """DELETE FROM inbound_message_claims
                   WHERE channel=? AND completed_at_ms < ?""",
                (self.channel, cutoff_ms),
            )

    async def close(self) -> None:
        """Release this owner's unfinished claims and flush expired records."""
        with open_state_database(self._state_dir) as connection:
            connection.execute(
                """DELETE FROM inbound_message_claims
                   WHERE channel=? AND owner=? AND completed_at_ms IS NULL""",
                (self.channel, self._owner),
            )
        await self.flush()


__all__ = [
    "DEDUP_FLUSH_INTERVAL",
    "DEDUP_FLUSH_THRESHOLD",
    "DEDUP_MAX_SIZE",
    "DEDUP_TTL_MS",
    "InboundDeduplicator",
]
