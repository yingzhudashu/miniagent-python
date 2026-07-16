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
TuiTheme = Literal["auto", "dark", "light"]


@dataclass(frozen=True, slots=True)
class TuiEvent:
    """从 TUI 控件发送到应用动作边界的不可变事件。"""

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
    theme: TuiTheme = "auto"
    input_mode: str = "single-turn"


@dataclass(frozen=True, slots=True)
class TuiUpdate:
    """Explicit partial state update emitted by an Assistant adapter."""

    changes: dict[str, Any]


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
        """返回当前不可变渲染快照。"""
        return self._snapshot

    def publish(self, snapshot: TuiSnapshot) -> None:
        """整体发布新的渲染快照。"""
        self._snapshot = snapshot

    def apply(self, update: TuiUpdate) -> TuiSnapshot:
        """应用显式的部分状态更新。"""
        return self.update(**update.changes)

    def update(self, **changes: Any) -> TuiSnapshot:
        """替换指定快照字段并返回新快照。"""
        self._snapshot = replace(self._snapshot, **changes)
        return self._snapshot

    def toggle_reasoning(self) -> bool:
        """切换推理内容展开状态并返回新值。"""
        value = not self._snapshot.reasoning_expanded
        self.update(reasoning_expanded=value)
        return value

    def __getattr__(self, name: str) -> Any:
        snapshot = self.__dict__.get("_snapshot")
        if snapshot is not None and hasattr(snapshot, name):
            return getattr(snapshot, name)
        raise AttributeError(name)

    async def dispatch(self, event: TuiEvent) -> None:
        """将框架无关事件分派到 Assistant 动作接口。"""
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


__all__ = [
    "TuiActions",
    "TuiApp",
    "TuiEvent",
    "TuiEventKind",
    "TuiSnapshot",
    "TuiTheme",
    "TuiUpdate",
]
