"""Disk persistence owned by the assistant session subsystem."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from typing import Any

from miniagent.agent.logging import get_logger
from miniagent.agent.types.config import normalize_conversation_history
from miniagent.assistant.infrastructure.atomic_json import atomic_dump_json
from miniagent.assistant.infrastructure.json_config import get_config
from miniagent.assistant.infrastructure.persistence import dump_state_file, load_state_file

_logger = get_logger(__name__)
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
class _DiskSessionConfig:
    dir_name: str
    workspace_path: str
    session_id: str
    session_number: int
    title: str
    created_at: str
    last_active: str


@dataclass(frozen=True, slots=True)
class _DiskConfigCacheEntry:
    mtime_ns: int
    size: int
    config: _DiskSessionConfig | None


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


def load_history_json_file(
    path: str,
    *,
    max_messages: int | None = None,
) -> list[dict[str, Any]]:
    """Load, validate, normalize, and bound one session history document."""
    if not os.path.isfile(path):
        return []
    try:
        file_size = os.path.getsize(path)
        document = load_state_file("session_history", path)
        history = normalize_conversation_history(document.get("messages"))
        original_count = len(history)
        history = truncate_history(history, max_messages=max_messages)
        if original_count > len(history):
            _logger.info(
                "history.json 已截断加载: %s (%d → %d 条)",
                path,
                original_count,
                len(history),
            )
        elif file_size > 5 * 1024 * 1024:
            _logger.info(
                "history.json 较大 (%d MB)，已加载最近 %d 条: %s",
                file_size // (1024 * 1024),
                len(history),
                path,
            )
        return history
    except (json.JSONDecodeError, OSError, ValueError, TypeError) as error:
        _logger.warning("history.json 加载失败，将使用空历史: %s → %s", path, error)
    except Exception as error:
        _logger.warning("history.json 加载失败，将使用空历史: %s → %s", path, error)
    return []


class SessionDiskStorage:
    """Own session paths, schema persistence, and fingerprinted config scanning."""

    def __init__(self, workspaces_dir: str, *, config_cache_max: int) -> None:
        self.workspaces_dir = workspaces_dir
        self.config_cache: dict[str, _DiskConfigCacheEntry] = {}
        self.config_cache_max = config_cache_max
        self.config_cache_lock = threading.Lock()

    def ensure_dir(self) -> None:
        """Create the configured session root if it does not exist."""
        os.makedirs(self.workspaces_dir, exist_ok=True)

    def scan_configs(self, *, cache_max: int | None = None) -> list[_DiskSessionConfig]:
        """Scan session configs while reusing unchanged parses."""
        limit = self.config_cache_max if cache_max is None else cache_max
        result: list[_DiskSessionConfig] = []
        seen_paths: set[str] = set()
        with self.config_cache_lock:
            try:
                entries = os.scandir(self.workspaces_dir)
            except OSError:
                return result
            with entries:
                for entry in entries:
                    parsed = self._scan_entry(entry, seen_paths, limit)
                    if parsed is not None:
                        result.append(parsed)
            for path in self.config_cache.keys() - seen_paths:
                self.config_cache.pop(path, None)
        return result

    def _scan_entry(
        self,
        entry: os.DirEntry[str],
        seen_paths: set[str],
        cache_max: int,
    ) -> _DiskSessionConfig | None:
        try:
            if not entry.is_dir():
                return None
        except OSError:
            return None
        config_path = os.path.join(entry.path, "config.json")
        try:
            stat = os.stat(config_path)
        except OSError:
            return None
        seen_paths.add(config_path)
        cached = self.config_cache.get(config_path)
        if cached and cached.mtime_ns == stat.st_mtime_ns and cached.size == stat.st_size:
            return cached.config
        parsed = self._parse_config(entry.name, entry.path, config_path)
        if cached is not None or len(self.config_cache) < cache_max:
            self.config_cache[config_path] = _DiskConfigCacheEntry(
                mtime_ns=stat.st_mtime_ns,
                size=stat.st_size,
                config=parsed,
            )
        return parsed

    @staticmethod
    def _parse_config(
        dir_name: str,
        workspace_path: str,
        config_path: str,
    ) -> _DiskSessionConfig | None:
        try:
            raw = load_state_file("session_config", config_path)
            number = raw.get("session_number", 0)
            return _DiskSessionConfig(
                dir_name=dir_name,
                workspace_path=workspace_path,
                session_id=str(raw.get("session_id") or ""),
                session_number=number if isinstance(number, int) else 0,
                title=str(raw.get("title") or ""),
                created_at=str(raw.get("created_at") or ""),
                last_active=str(raw.get("last_active") or ""),
            )
        except Exception as error:
            _logger.debug("扫描磁盘配置失败: %s", error)
            return None

    def save_config(self, config: SessionConfig, *, cache_max: int | None = None) -> None:
        """Atomically persist one session config and refresh its scan cache entry."""
        config_path = os.path.join(config.workspace_path, "config.json")
        try:
            atomic_dump_json(
                config_path,
                {
                    "schema_version": 1,
                    "session_id": config.session_id,
                    "workspace_path": config.workspace_path,
                    "files_path": config.files_path,
                    "skills_path": config.skills_path,
                    "created_at": config.created_at,
                    "last_active": config.last_active,
                    "session_number": config.session_number,
                    "title": config.title,
                    "description": config.description,
                    "chat_id": config.chat_id,
                    "sender_id": config.sender_id,
                },
                indent=2,
                ensure_ascii=False,
            )
            self._cache_saved_config(config_path, config, cache_max=cache_max)
        except Exception:
            _logger.exception("会话配置保存失败: %s", config.workspace_path)

    def _cache_saved_config(
        self,
        config_path: str,
        config: SessionConfig,
        *,
        cache_max: int | None,
    ) -> None:
        try:
            stat = os.stat(config_path)
        except OSError:
            with self.config_cache_lock:
                self.config_cache.pop(config_path, None)
            return
        parsed = _DiskSessionConfig(
            dir_name=os.path.basename(config.workspace_path),
            workspace_path=config.workspace_path,
            session_id=config.session_id,
            session_number=config.session_number,
            title=config.title,
            created_at=config.created_at,
            last_active=config.last_active,
        )
        limit = self.config_cache_max if cache_max is None else cache_max
        with self.config_cache_lock:
            if config_path in self.config_cache or len(self.config_cache) < limit:
                self.config_cache[config_path] = _DiskConfigCacheEntry(
                    mtime_ns=stat.st_mtime_ns,
                    size=stat.st_size,
                    config=parsed,
                )

    @staticmethod
    def load_history(
        config: SessionConfig,
        *,
        max_messages: int | None = None,
    ) -> list[dict[str, Any]]:
        """Load one history document using the caller's active size bound."""
        return load_history_json_file(
            os.path.join(config.workspace_path, "history.json"),
            max_messages=max_messages,
        )

    @staticmethod
    def save_history(config: SessionConfig, history: list[dict[str, Any]]) -> None:
        """Persist one complete history document with the current schema."""
        dump_state_file(
            "session_history",
            os.path.join(config.workspace_path, "history.json"),
            {"message_format": "miniagent-conversation-v1", "messages": history},
            ensure_ascii=False,
            indent=2,
        )

    def list_session_ids(self) -> list[str]:
        """Return directory ids containing a session config document."""
        result: list[str] = []
        try:
            entries = os.scandir(self.workspaces_dir)
        except OSError:
            return result
        with entries:
            for entry in entries:
                try:
                    if entry.is_dir() and os.path.isfile(os.path.join(entry.path, "config.json")):
                        result.append(entry.name)
                except OSError:
                    continue
        return result


__all__ = [
    "MAX_HISTORY_MESSAGES",
    "SessionConfig",
    "SessionDiskStorage",
    "_DiskConfigCacheEntry",
    "_DiskSessionConfig",
    "load_history_json_file",
    "truncate_history",
]
