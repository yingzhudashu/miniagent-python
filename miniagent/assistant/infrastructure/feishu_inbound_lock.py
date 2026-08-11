"""SQLite lease for the single Feishu inbound WebSocket owner."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from miniagent.agent.constants import INSTANCE_HEARTBEAT_TIMEOUT
from miniagent.agent.logging import get_logger
from miniagent.agent.types.error_prefix import ERROR_PREFIX
from miniagent.assistant.infrastructure.process_utils import is_process_running
from miniagent.assistant.state.sync import immediate_transaction, open_state_database

_logger = get_logger(__name__)
_RESOURCE = "feishu:inbound"
_TTL_MS = int(INSTANCE_HEARTBEAT_TIMEOUT * 1000)


def _state_root(state_dir: str | None) -> Path:
    from miniagent.assistant.infrastructure.paths import resolve_state_dir

    return Path(state_dir or resolve_state_dir())


def _owner(pid: int, instance_id: int | None) -> str:
    return f"{pid}:{'' if instance_id is None else instance_id}"


def _parse_owner(owner: str) -> tuple[int, int | None]:
    pid_text, _, instance_text = owner.partition(":")
    return int(pid_text), int(instance_text) if instance_text else None


def try_acquire_feishu_inbound_owner(
    *,
    state_dir: str | None = None,
    instance_id: int | None = None,
) -> tuple[bool, str]:
    """Atomically acquire or renew the current Feishu inbound lease."""
    me = os.getpid()
    mine = _owner(me, instance_id)
    now_ms = int(time.time() * 1000)
    with open_state_database(_state_root(state_dir)) as connection:
        with immediate_transaction(connection):
            row = connection.execute(
                "SELECT owner, expires_at_ms, generation FROM process_leases WHERE resource=?",
                (_RESOURCE,),
            ).fetchone()
            generation = 1
            if row is not None:
                old_owner = str(row[0])
                old_pid, old_instance_id = _parse_owner(old_owner)
                generation = int(row[2]) + (0 if old_owner == mine else 1)
                if (
                    old_owner != mine
                    and int(row[1]) > now_ms
                    and is_process_running(old_pid)
                ):
                    return (
                        False,
                        f"{ERROR_PREFIX} 飞书入站已被实例 "
                        f"#{old_instance_id if old_instance_id is not None else '?'}"
                        f"（PID={old_pid}）占用；请在该实例执行 `.feishu stop` "
                        "或停止该进程后再试。",
                    )
            connection.execute(
                """INSERT INTO process_leases VALUES (?, ?, ?, ?)
                   ON CONFLICT(resource) DO UPDATE SET owner=excluded.owner,
                     expires_at_ms=excluded.expires_at_ms,
                     generation=excluded.generation""",
                (_RESOURCE, mine, now_ms + _TTL_MS, generation),
            )
    _logger.info("已获取飞书入站独占 lease (PID=%s)", me)
    return True, ""


def renew_feishu_inbound_owner(state_dir: str | None = None) -> bool:
    """Renew the lease when this process currently owns it."""
    now_ms = int(time.time() * 1000)
    with open_state_database(_state_root(state_dir)) as connection:
        row = connection.execute(
            "SELECT owner FROM process_leases WHERE resource=?",
            (_RESOURCE,),
        ).fetchone()
        if row is None:
            return False
        pid, _instance_id = _parse_owner(str(row[0]))
        if pid != os.getpid():
            return False
        cursor = connection.execute(
            "UPDATE process_leases SET expires_at_ms=? WHERE resource=? AND owner=?",
            (now_ms + _TTL_MS, _RESOURCE, str(row[0])),
        )
        return cursor.rowcount == 1


def read_feishu_inbound_owner(
    state_dir: str | None = None,
) -> dict[str, Any] | None:
    """Read the current unexpired owner, removing stale leases."""
    now_ms = int(time.time() * 1000)
    with open_state_database(_state_root(state_dir)) as connection:
        with immediate_transaction(connection):
            row = connection.execute(
                "SELECT owner, expires_at_ms FROM process_leases WHERE resource=?",
                (_RESOURCE,),
            ).fetchone()
            if row is None:
                return None
            pid, instance_id = _parse_owner(str(row[0]))
            alive = int(row[1]) > now_ms and is_process_running(pid)
            if not alive:
                connection.execute(
                    "DELETE FROM process_leases WHERE resource=?",
                    (_RESOURCE,),
                )
                return None
            return {
                "pid": pid,
                "instance_id": instance_id,
                "alive": True,
                "expires_at_ms": int(row[1]),
            }


def release_feishu_inbound_owner(state_dir: str | None = None) -> None:
    """Release the lease only when it belongs to this process."""
    with open_state_database(_state_root(state_dir)) as connection:
        row = connection.execute(
            "SELECT owner FROM process_leases WHERE resource=?",
            (_RESOURCE,),
        ).fetchone()
        if row is None:
            return
        pid, _instance_id = _parse_owner(str(row[0]))
        if pid == os.getpid():
            connection.execute(
                "DELETE FROM process_leases WHERE resource=? AND owner=?",
                (_RESOURCE, str(row[0])),
            )


__all__ = [
    "try_acquire_feishu_inbound_owner",
    "renew_feishu_inbound_owner",
    "release_feishu_inbound_owner",
    "read_feishu_inbound_owner",
]
