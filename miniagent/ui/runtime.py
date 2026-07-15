"""Framework-neutral contracts for composing the reusable terminal UI."""

from __future__ import annotations

import inspect
from dataclasses import dataclass, replace
from typing import Any, Literal, Protocol, runtime_checkable

TuiEventKind = Literal[
    "submit",
    "cancel",
    "command",
    "model",
    "session",
    "copy",
    "resize",
]


@dataclass(frozen=True, slots=True)
class TuiEvent:
    kind: TuiEventKind
    value: Any = None


@dataclass(frozen=True, slots=True)
class TuiSnapshot:
    """Immutable product-neutral state consumed by TUI render functions."""

    transcript: tuple[Any, ...] = ()
    input_text: str = ""
    status: str = "就绪"
    busy: bool = False
    reasoning_expanded: bool = True
    queued_messages: int = 0
    context_tokens_used: int = 0
    context_window: int = 0
    provider: str = ""
    model: str = ""


@runtime_checkable
class TuiActions(Protocol):
    """Assistant-owned actions invoked by user interactions."""

    async def submit(self, text: str) -> None: ...
    async def cancel(self) -> None: ...
    async def command(self, text: str) -> None: ...
    async def select_model(self, profile: str) -> None: ...
    async def select_session(self, session_id: str) -> None: ...
    async def copy(self, text: str) -> None: ...


class TuiApp:
    """Small interaction boundary shared by prompt-toolkit and test adapters."""

    def __init__(self, actions: TuiActions, snapshot: TuiSnapshot | None = None) -> None:
        self._actions = actions
        self._snapshot = snapshot or TuiSnapshot()

    @property
    def snapshot(self) -> TuiSnapshot:
        return self._snapshot

    def publish(self, snapshot: TuiSnapshot) -> None:
        self._snapshot = snapshot

    def update(self, **changes: Any) -> TuiSnapshot:
        self._snapshot = replace(self._snapshot, **changes)
        return self._snapshot

    async def dispatch(self, event: TuiEvent) -> None:
        if event.kind == "resize":
            return
        method_name = {
            "model": "select_model",
            "session": "select_session",
        }.get(event.kind, event.kind)
        action = getattr(self._actions, method_name, None)
        if not callable(action):
            raise ValueError(f"unsupported TUI event: {event.kind}")
        result = action() if event.kind == "cancel" else action(event.value)
        if inspect.isawaitable(result):
            await result


__all__ = ["TuiActions", "TuiApp", "TuiEvent", "TuiEventKind", "TuiSnapshot"]
