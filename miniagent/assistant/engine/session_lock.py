"""Transactional cross-process ownership for user sessions."""

from __future__ import annotations

import asyncio
import os
import threading
import time

from miniagent.agent.constants import INSTANCE_HEARTBEAT_TIMEOUT
from miniagent.assistant.infrastructure.process_utils import is_process_running
from miniagent.assistant.session.manager import _get_workspaces_dir
from miniagent.assistant.state.sync import immediate_transaction, open_state_database
from miniagent.assistant.utils.session_id import safe_session_id

_LEASE_SECONDS = INSTANCE_HEARTBEAT_TIMEOUT
_HELD_RESOURCES: set[str] = set()
_HELD_RESOURCES_LOCK = threading.Lock()


def _state_dir() -> str:
    return os.path.dirname(_get_workspaces_dir())


def _resource(session_id: str) -> str:
    return f"session:{safe_session_id(session_id)}"


def try_lock_session(session_id: str) -> tuple[bool, str]:
    """Acquire or renew one session lease atomically."""
    owner = str(os.getpid())
    resource = _resource(session_id)
    now_ms = int(time.time() * 1000)
    expires_at_ms = now_ms + _LEASE_SECONDS * 1000
    with open_state_database(_state_dir()) as connection:
        with immediate_transaction(connection):
            row = connection.execute(
                """SELECT owner, expires_at_ms, generation FROM process_leases
                   WHERE resource=?""",
                (resource,),
            ).fetchone()
            if row is not None and str(row[0]) != owner and int(row[1]) > now_ms:
                try:
                    locked_pid = int(row[0])
                except ValueError:
                    locked_pid = 0
                if locked_pid and is_process_running(locked_pid):
                    return False, f"被其他实例占用 (PID={locked_pid})"
            generation = int(row[2]) + 1 if row is not None else 1
            connection.execute(
                """INSERT INTO process_leases VALUES (?, ?, ?, ?)
                   ON CONFLICT(resource) DO UPDATE SET
                     owner=excluded.owner,
                     expires_at_ms=excluded.expires_at_ms,
                     generation=excluded.generation""",
                (resource, owner, expires_at_ms, generation),
            )
    with _HELD_RESOURCES_LOCK:
        _HELD_RESOURCES.add(resource)
    return True, ""


async def try_lock_session_async(session_id: str) -> tuple[bool, str]:
    """Acquire a session lease without blocking the event loop."""
    return await asyncio.to_thread(try_lock_session, session_id)


def release_session_lock(session_id: str) -> None:
    """Release a session lease only when the current process owns it."""
    with open_state_database(_state_dir()) as connection:
        connection.execute(
            "DELETE FROM process_leases WHERE resource=? AND owner=?",
            (_resource(session_id), str(os.getpid())),
        )
    with _HELD_RESOURCES_LOCK:
        _HELD_RESOURCES.discard(_resource(session_id))


def renew_session_leases() -> int:
    """Renew all session leases currently held by this process."""
    with _HELD_RESOURCES_LOCK:
        resources = tuple(_HELD_RESOURCES)
    if not resources:
        return 0
    owner = str(os.getpid())
    expires_at_ms = int((time.time() + _LEASE_SECONDS) * 1000)
    renewed = 0
    with open_state_database(_state_dir()) as connection:
        with immediate_transaction(connection):
            for resource in resources:
                cursor = connection.execute(
                    """UPDATE process_leases SET expires_at_ms=?
                       WHERE resource=? AND owner=?""",
                    (expires_at_ms, resource, owner),
                )
                renewed += max(0, cursor.rowcount)
    return renewed


def _session_lock_owner(session_id: str) -> int | None:
    """Return any live owner PID for listings and conflict checks."""
    with open_state_database(_state_dir()) as connection:
        row = connection.execute(
            "SELECT owner, expires_at_ms FROM process_leases WHERE resource=?",
            (_resource(session_id),),
        ).fetchone()
    if row is None or int(row[1]) <= int(time.time() * 1000):
        return None
    try:
        locked_pid = int(row[0])
    except ValueError:
        return None
    return locked_pid if is_process_running(locked_pid) else None


def is_session_locked(session_id: str) -> int | None:
    """Return the live foreign owner PID, otherwise ``None``."""
    owner = _session_lock_owner(session_id)
    return None if owner == os.getpid() else owner


__all__ = [
    "is_session_locked",
    "release_session_lock",
    "renew_session_leases",
    "try_lock_session",
    "try_lock_session_async",
]
