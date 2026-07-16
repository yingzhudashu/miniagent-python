"""Pure session-key classification shared by Agent and Assistant."""

from __future__ import annotations

_BACKGROUND_PREFIX = "__bg__"


def is_background_session_key(session_key: str) -> bool:
    """判断会话键是否属于内部后台任务。"""
    return (session_key or "").startswith(_BACKGROUND_PREFIX)


__all__ = ["is_background_session_key"]
