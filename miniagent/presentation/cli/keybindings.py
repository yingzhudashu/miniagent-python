"""Validated action-to-key configuration for the enhanced TUI."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

DEFAULT_TUI_KEYBINDINGS: dict[str, str] = {
    "model_selector": "c-p",
    "session_selector": "c-o",
    "toggle_reasoning": "c-r",
    "tasks": "c-t",
    "copy_mode": "c-m",
    "newline": "escape enter",
}


def resolve_tui_keybindings(value: Any) -> dict[str, str]:
    """Merge known actions and reject collisions before prompt-toolkit setup."""
    result = dict(DEFAULT_TUI_KEYBINDINGS)
    if value is not None and not isinstance(value, Mapping):
        raise ValueError("cli.keybindings must be an object")
    for action, key in dict(value or {}).items():
        if action not in result:
            raise ValueError(f"unknown cli keybinding action: {action}")
        normalized = str(key or "").strip().lower()
        if not normalized:
            raise ValueError(f"empty cli keybinding for action: {action}")
        result[action] = normalized
    by_key: dict[str, str] = {}
    for action, key in result.items():
        previous = by_key.get(key)
        if previous is not None:
            raise ValueError(
                f"conflicting cli keybinding {key!r}: {previous} and {action}"
            )
        by_key[key] = action
    return result


__all__ = ["DEFAULT_TUI_KEYBINDINGS", "resolve_tui_keybindings"]
