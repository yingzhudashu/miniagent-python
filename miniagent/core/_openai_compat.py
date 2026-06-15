"""Shared OpenAI compatibility helpers.

Extracts patterns that were previously duplicated across planner.py and task_classifier.py.
"""

from __future__ import annotations

from typing import Any

try:
    from openai import BadRequestError as _OpenAIBadRequestError
except ImportError:
    _OpenAIBadRequestError = None  # type: ignore[misc, assignment]


_JSON_OBJECT_USER_HINT = "Please return a valid JSON object."


def _content_has_json(content: Any) -> bool:
    if isinstance(content, str):
        return "json" in content.lower()
    if isinstance(content, list):
        for part in content:
            if isinstance(part, str) and "json" in part.lower():
                return True
            if isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str) and "json" in text.lower():
                    return True
        return False
    return False


def ensure_json_object_user_message(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return a copied message list whose user content satisfies JSON mode.

    Some OpenAI-compatible endpoints validate only user/input messages when
    ``response_format={"type": "json_object"}`` is used. System prompts in this
    project already request JSON, but the user message must also mention JSON.
    """
    copied = [dict(message) for message in messages if isinstance(message, dict)]
    user_indexes = [i for i, message in enumerate(copied) if message.get("role") == "user"]

    if any(_content_has_json(copied[i].get("content")) for i in user_indexes):
        return copied

    for i in reversed(user_indexes):
        content = copied[i].get("content")
        if isinstance(content, str):
            separator = "\n\n" if content else ""
            copied[i]["content"] = f"{content}{separator}{_JSON_OBJECT_USER_HINT}"
            return copied
        if isinstance(content, list):
            next_content = list(content)
            for j in range(len(next_content) - 1, -1, -1):
                part = next_content[j]
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    text = part["text"]
                    separator = "\n\n" if text else ""
                    next_part = dict(part)
                    next_part["text"] = f"{text}{separator}{_JSON_OBJECT_USER_HINT}"
                    next_content[j] = next_part
                    copied[i]["content"] = next_content
                    return copied
            next_content.append({"type": "text", "text": _JSON_OBJECT_USER_HINT})
            copied[i]["content"] = next_content
            return copied

    copied.append({"role": "user", "content": _JSON_OBJECT_USER_HINT})
    return copied


def json_object_requires_json_keyword(err: Exception) -> bool:
    """Return True for the JSON mode error fixed by adding a user JSON hint."""
    low = str(err).lower()
    return (
        "must contain the word" in low
        and "json" in low
        and ("json_object" in low or "response_format" in low or "format" in low)
    )


def json_object_unsupported(err: Exception) -> bool:
    """Return True if *err* indicates the endpoint doesn't support ``response_format=json_object``.

    Callers can use this to decide whether to retry without JSON mode.
    """
    low = str(err).lower()
    if json_object_requires_json_keyword(err):
        return False
    mentions_json_format = "response_format" in low or "json_object" in low
    if _OpenAIBadRequestError is not None and isinstance(err, _OpenAIBadRequestError):
        return mentions_json_format
    if not mentions_json_format:
        return False
    unsupported_markers = (
        "unsupported",
        "not support",
        "not supported",
        "does not support",
        "invalid parameter",
        "unrecognized",
        "unknown parameter",
    )
    return any(marker in low for marker in unsupported_markers)


__all__ = [
    "ensure_json_object_user_message",
    "json_object_requires_json_keyword",
    "json_object_unsupported",
]
