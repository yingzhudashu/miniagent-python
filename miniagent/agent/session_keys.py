"""Pure session-key classification shared by Agent and Assistant."""

from __future__ import annotations

_BACKGROUND_PREFIX = "__bg__"


def is_background_session_key(session_key: str) -> bool:
    return (session_key or "").startswith(_BACKGROUND_PREFIX)


__all__ = ["is_background_session_key"]
