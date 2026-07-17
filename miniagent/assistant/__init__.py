"""Personal-assistant product layer and process composition root."""

from __future__ import annotations

from typing import Any

_PUBLIC = frozenset(
    {
        "AssistantApplication",
        "AssistantSpec",
        "PersonalAssistantSpec",
        "create_assistant",
        "create_assistant_application",
        "create_personal_assistant",
        "personal_assistant_spec",
        "run_assistant",
    }
)


def __getattr__(name: str) -> Any:
    if name not in _PUBLIC:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    if name in {"AssistantSpec", "PersonalAssistantSpec"}:
        from miniagent.assistant import spec

        value = getattr(spec, name)
        globals()[name] = value
        return value
    from miniagent.assistant import app

    value = getattr(app, name)
    globals()[name] = value
    return value

__all__ = [
    "AssistantApplication",
    "AssistantSpec",
    "PersonalAssistantSpec",
    "create_assistant",
    "create_assistant_application",
    "create_personal_assistant",
    "personal_assistant_spec",
    "run_assistant",
]
