"""SQLite persistence owned by the assistant session subsystem."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from miniagent.agent.types.config import normalize_conversation_history
from miniagent.assistant.infrastructure.json_config import get_config
from miniagent.assistant.state.sync import immediate_transaction, open_state_database

MAX_HISTORY_MESSAGES = 200


@dataclass
class SessionConfig:
    """Persisted identity, paths, timestamps, and display metadata for one session."""

    session_id: str
    workspace_path: str
    files_path: str
    skills_path: str
    created_at: str
    last_active: str
    session_number: int = 0
    title: str = ""
    description: str = ""
    chat_id: str | None = None
    sender_id: str | None = None


@dataclass(frozen=True, slots=True)
class StoredSessionConfig:
    """Lightweight session metadata used by listings."""

    dir_name: str
    workspace_path: str
    session_id: str
    session_number: int
    title: str
    created_at: str
    last_active: str


def truncate_history(
    history: list[dict[str, Any]],
    max_messages: int | None = None,
) -> list[dict[str, Any]]:
    """Bound history while preserving system messages and the first user message."""
    if max_messages is None:
        max_messages = int(get_config("memory.max_history_messages", MAX_HISTORY_MESSAGES))
    max_messages = max(1, max_messages)
    if len(history) <= max_messages:
        return history
    system_messages = [message for message in history if message.get("role") == "system"]
    other_messages = [message for message in history if message.get("role") != "system"]
    if system_messages and len(other_messages) > max_messages - 1:
        first_user = next(
            (message for message in other_messages if message.get("role") == "user"),
            None,
        )
        tail_size = max_messages - len(system_messages) - (1 if first_user else 0)
        tail = other_messages[-tail_size:] if tail_size > 0 else []
        return system_messages + ([first_user] if first_user else []) + tail
    return history[-max_messages:]


def _to_ms(value: str) -> int:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    return int(datetime.fromisoformat(normalized).timestamp() * 1000)


def _to_iso(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat()


class SessionStorage:
    """Persist session metadata and history while retaining workspace file roots."""

    def __init__(self, workspaces_dir: str) -> None:
        self.workspaces_dir = workspaces_dir
        self.state_dir = str(Path(workspaces_dir).parent)

    def ensure_dir(self) -> None:
        Path(self.workspaces_dir).mkdir(parents=True, exist_ok=True)
        with open_state_database(self.state_dir):
            pass

    def scan_configs(self) -> list[StoredSessionConfig]:
        with open_state_database(self.state_dir) as connection:
            rows = connection.execute(
                """SELECT session_id, workspace_path, session_number, title,
                          created_at_ms, updated_at_ms
                   FROM sessions ORDER BY session_number, session_id"""
            ).fetchall()
        return [
            StoredSessionConfig(
                dir_name=Path(str(row[1])).name,
                workspace_path=str(row[1]),
                session_id=str(row[0]),
                session_number=int(row[2]),
                title=str(row[3]),
                created_at=_to_iso(int(row[4])),
                last_active=_to_iso(int(row[5])),
            )
            for row in rows
        ]

    def save_config(self, config: SessionConfig) -> None:
        created_ms = _to_ms(config.created_at)
        updated_ms = _to_ms(config.last_active)
        with open_state_database(self.state_dir) as connection:
            connection.execute(
                """INSERT INTO sessions(
                       session_id, title, description, workspace_path, files_path,
                       skills_path, session_number, chat_id, sender_id,
                       created_at_ms, updated_at_ms, next_sequence
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                   ON CONFLICT(session_id) DO UPDATE SET
                     title=excluded.title,
                     description=excluded.description,
                     workspace_path=excluded.workspace_path,
                     files_path=excluded.files_path,
                     skills_path=excluded.skills_path,
                     session_number=excluded.session_number,
                     chat_id=excluded.chat_id,
                     sender_id=excluded.sender_id,
                     updated_at_ms=excluded.updated_at_ms""",
                (
                    config.session_id,
                    config.title,
                    config.description,
                    config.workspace_path,
                    config.files_path,
                    config.skills_path,
                    config.session_number,
                    config.chat_id,
                    config.sender_id,
                    created_ms,
                    updated_ms,
                ),
            )

    def get_config(self, session_id: str) -> SessionConfig | None:
        with open_state_database(self.state_dir) as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE session_id=?", (session_id,)
            ).fetchone()
        if row is None:
            return None
        return SessionConfig(
            session_id=str(row["session_id"]),
            workspace_path=str(row["workspace_path"]),
            files_path=str(row["files_path"]),
            skills_path=str(row["skills_path"]),
            created_at=_to_iso(int(row["created_at_ms"])),
            last_active=_to_iso(int(row["updated_at_ms"])),
            session_number=int(row["session_number"]),
            title=str(row["title"]),
            description=str(row["description"]),
            chat_id=row["chat_id"],
            sender_id=row["sender_id"],
        )

    def load_history(
        self,
        config: SessionConfig,
        *,
        max_messages: int | None = None,
    ) -> list[dict[str, Any]]:
        with open_state_database(self.state_dir) as connection:
            rows = connection.execute(
                """SELECT content_json FROM messages
                   WHERE session_id=? ORDER BY sequence""",
                (config.session_id,),
            ).fetchall()
        history = normalize_conversation_history(
            [json.loads(str(row[0])) for row in rows]
        )
        return truncate_history(history, max_messages=max_messages)

    def save_history(self, config: SessionConfig, history: list[dict[str, Any]]) -> None:
        with open_state_database(self.state_dir) as connection:
            with immediate_transaction(connection):
                connection.execute("DELETE FROM messages WHERE session_id=?", (config.session_id,))
                connection.executemany(
                    """INSERT INTO messages(
                           session_id, sequence, message_id, role, content_json, created_at_ms
                       ) VALUES (?, ?, ?, ?, ?, ?)""",
                    [
                        (
                            config.session_id,
                            sequence,
                            f"history:{sequence}",
                            str(message.get("role") or "unknown"),
                            json.dumps(
                                message,
                                ensure_ascii=False,
                                separators=(",", ":"),
                                allow_nan=False,
                            ),
                            int(datetime.now(timezone.utc).timestamp() * 1000),
                        )
                        for sequence, message in enumerate(history, start=1)
                    ],
                )
                connection.execute(
                    """UPDATE sessions SET next_sequence=?, updated_at_ms=?
                       WHERE session_id=?""",
                    (
                        len(history) + 1,
                        _to_ms(config.last_active),
                        config.session_id,
                    ),
                )

    def list_session_ids(self) -> list[str]:
        return [entry.session_id for entry in self.scan_configs()]

    def delete_session(self, session_id: str) -> bool:
        with open_state_database(self.state_dir) as connection:
            cursor = connection.execute("DELETE FROM sessions WHERE session_id=?", (session_id,))
            return cursor.rowcount == 1


__all__ = [
    "MAX_HISTORY_MESSAGES",
    "SessionConfig",
    "SessionStorage",
    "StoredSessionConfig",
    "truncate_history",
]
