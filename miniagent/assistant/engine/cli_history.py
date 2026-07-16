"""CLI input-history persistence and session-history preloading."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Iterable, Mapping
from typing import Any

from miniagent.assistant.infrastructure.json_config import get_config

_logger = logging.getLogger(__name__)


def resolve_cli_history_file() -> str:
    """Return the state-rooted history file shared by TUI and fallback CLI."""
    from miniagent.assistant.infrastructure.paths import resolve_state_dir

    history_dir = os.path.join(resolve_state_dir(), "cli")
    os.makedirs(history_dir, exist_ok=True)
    return os.path.join(history_dir, "history.txt")


def create_cli_file_history(filename: str) -> Any:
    """Create a FileHistory that ensures its parent exists before writes."""
    from prompt_toolkit.history import FileHistory

    class SafeFileHistory(FileHistory):
        """FileHistory with memory-only merging for session user messages."""

        def store_string(self, string: str) -> None:
            parent_dir = os.path.dirname(self.filename)
            if parent_dir:
                os.makedirs(parent_dir, exist_ok=True)
            super().store_string(string)

        def merge_strings_memory_only(self, strings: Iterable[str]) -> None:
            """Merge unique entries without writing them to ``history.txt``."""
            if not getattr(self, "_loaded", False):
                self._loaded_strings = list(self.load_history_strings())
                self._loaded = True
            known = {(x or "").strip() for x in self._loaded_strings if (x or "").strip()}
            for raw in reversed(list(strings)):
                value = (raw or "").strip()
                if not value or value in known:
                    continue
                self._loaded_strings.insert(0, value)
                known.add(value)

    return SafeFileHistory(filename)


def cli_input_history_max() -> int:
    """Return the number of session user messages exposed through arrow history."""
    return max(1, int(get_config("cli.input_history_max", 100)))


def session_user_inputs_for_cli_history(
    state: Mapping[str, Any],
    *,
    limit: int | None = None,
) -> list[str]:
    """Collect current-session user messages in chronological order."""
    session_manager = state.get("session_manager")
    session_id = state.get("active_session_id", "")
    if session_manager is None or not session_id:
        return []
    session = session_manager.get(session_id)
    if session is None:
        return []

    from miniagent.assistant.engine.commands.session_management import (
        _load_session_history_messages,
    )

    result = [
        str(message.get("content") or "").strip()
        for message in _load_session_history_messages(session)
        if isinstance(message, dict)
        and message.get("role") == "user"
        and str(message.get("content") or "").strip()
    ]
    max_items = limit if limit is not None else cli_input_history_max()
    return result[-max_items:]


def prime_cli_input_history_from_session(
    state: Mapping[str, Any],
    buffer: Any,
    *,
    limit: int | None = None,
) -> None:
    """Merge current-session user messages into memory-only input history."""
    merge = getattr(getattr(buffer, "history", None), "merge_strings_memory_only", None)
    if merge is None:
        return
    strings = session_user_inputs_for_cli_history(state, limit=limit)
    if not strings:
        return
    try:
        merge(strings)
    except Exception as error:
        _logger.warning("历史加载失败，继续启动: %s", error)


class _HistoryLoadDone:
    """Completed-task placeholder used when no asyncio loop is running."""

    def done(self) -> bool:
        """返回已完成状态，以兼容 ``asyncio.Task`` 的查询接口。"""
        return True

    def result(self) -> None:
        """已完成的占位任务没有返回值或异常。"""
        return None


def _mark_buffer_history_preloaded(buffer: Any) -> None:
    if getattr(buffer, "_load_history_task", None) is not None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        buffer._load_history_task = _HistoryLoadDone()
        return
    future = loop.create_future()
    future.set_result(None)
    buffer._load_history_task = future


def sync_preload_buffer_working_lines(buffer: Any) -> None:
    """Preload prompt_toolkit working lines so the first arrow press works."""
    history = getattr(buffer, "history", None)
    if history is None or not hasattr(history, "get_strings"):
        return
    strings = list(history.get_strings())
    current = buffer.text
    buffer._working_lines.clear()
    buffer._working_lines.extend(strings)
    buffer._working_lines.append(current)
    buffer.working_index = len(buffer._working_lines) - 1
    _mark_buffer_history_preloaded(buffer)


def reload_cli_input_history(
    state: Mapping[str, Any],
    buffer: Any,
    history_file: str,
    *,
    limit: int | None = None,
) -> None:
    """Rebuild a buffer history after a session switch."""
    buffer.history = create_cli_file_history(history_file)
    prime_cli_input_history_from_session(state, buffer, limit=limit)
    sync_preload_buffer_working_lines(buffer)


def prime_fallback_readline_history(history_file: str) -> None:
    """Load recent persisted entries into readline when it is available."""
    try:
        import readline
    except ImportError:
        return
    readline_module: Any = readline
    if not os.path.isfile(history_file):
        return
    try:
        lines: list[str] = []
        with open(history_file, encoding="utf-8") as stream:
            for line in stream:
                line = line.strip()
                if line.startswith("+") and len(line) > 1:
                    lines.append(line[1:])
        for entry in lines[-cli_input_history_max():]:
            readline_module.add_history(entry)
    except Exception as error:
        _logger.debug("fallback readline 历史预填充失败: %s", error)


__all__ = [
    "cli_input_history_max",
    "create_cli_file_history",
    "prime_cli_input_history_from_session",
    "prime_fallback_readline_history",
    "reload_cli_input_history",
    "resolve_cli_history_file",
    "session_user_inputs_for_cli_history",
    "sync_preload_buffer_working_lines",
]
