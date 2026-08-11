"""Personal-assistant product layer and process composition root."""

from __future__ import annotations

from typing import Any

_PUBLIC = frozenset(
    {
        "PersonalAssistantApplication",
        "AssistantSpec",
        "create_assistant",
        "create_personal_assistant",
        "run_assistant",
    }
)


def __getattr__(name: str) -> Any:
    if name not in _PUBLIC:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    if name == "AssistantSpec":
        from miniagent.assistant import spec

        value = getattr(spec, name)
        globals()[name] = value
        return value
    if name == "run_assistant":
        from miniagent.assistant.runner import run_assistant

        globals()[name] = run_assistant
        return run_assistant
    from miniagent.assistant import app

    value = getattr(app, name)
    globals()[name] = value
    return value

__all__ = [
    "PersonalAssistantApplication",
    "AssistantSpec",
    "create_assistant",
    "create_personal_assistant",
    "run_assistant",
]
