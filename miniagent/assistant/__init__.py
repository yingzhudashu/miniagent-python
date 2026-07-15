"""Personal-assistant product layer and process composition root."""

from __future__ import annotations

from typing import Any

_PUBLIC = frozenset(
    {"AssistantApplication", "create_assistant_application", "run_assistant"}
)


def __getattr__(name: str) -> Any:
    if name not in _PUBLIC:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from miniagent.assistant import app

    value = getattr(app, name)
    globals()[name] = value
    return value

__all__ = ["AssistantApplication", "create_assistant_application", "run_assistant"]
