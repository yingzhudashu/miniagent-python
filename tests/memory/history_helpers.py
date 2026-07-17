"""Shared helpers for history archive / progressive tests."""


def history_turn(user: str, assistant: str) -> list[dict]:
    return [
        {"role": "user", "content": user},
        {"role": "assistant", "content": assistant},
    ]
