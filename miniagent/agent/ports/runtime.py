"""Runtime ports used by the reusable Agent pipeline."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ActivityLogProtocol(Protocol):
    async def log_session_start(
        self, session_key: str, user_input: str, source: str
    ) -> None: ...
    async def log_llm_call(
        self,
        session_key: str,
        turn: int,
        model: str,
        message_count: int,
        tool_count: int,
        thinking: str | None,
        token_usage: dict[str, Any] | None,
    ) -> None: ...
    async def log_tool_call(
        self,
        session_key: str,
        tool_name: str,
        intent: str,
        args: dict[str, Any],
        result: str,
        duration_ms: int,
        success: bool,
    ) -> None: ...
    async def log_final_reply(self, session_key: str, reply: str) -> None: ...
    async def log_incomplete(self, session_key: str, reason: str) -> None: ...
    def get_stats(self) -> dict[str, Any]: ...
    def clear_old_entries(self, days: int = 30) -> int: ...


@runtime_checkable
class KeywordIndexProtocol(Protocol):
    def search_relevant(
        self, query: str, limit: int = 10, recent_minutes: int = 0
    ) -> list[Any]: ...
    def index_entry(self, session_key: str, entry: Any) -> None: ...
    def save(self) -> None: ...
    def get_stats(self) -> dict[str, Any]: ...


class OnThinkingCallback(Protocol):
    async def __call__(
        self,
        text: str,
        streaming: bool,
        header: str,
        *,
        full_record: str | None = None,
        reset: bool = False,
        is_last_step: bool = False,
    ) -> None: ...


class OnToolFinishCallback(Protocol):
    async def __call__(
        self,
        name: str,
        args_json: str,
        result: str,
        success: bool,
        *,
        thinking_header: str | None = None,
    ) -> None: ...


OnToolCall = Callable[[str, str, str], None]
OnPlan = Callable[[Any], Awaitable[Any]]
OnThinking = OnThinkingCallback
OnToolFinish = OnToolFinishCallback

__all__ = [
    "ActivityLogProtocol",
    "KeywordIndexProtocol",
    "OnPlan",
    "OnThinking",
    "OnThinkingCallback",
    "OnToolCall",
    "OnToolFinish",
    "OnToolFinishCallback",
]
