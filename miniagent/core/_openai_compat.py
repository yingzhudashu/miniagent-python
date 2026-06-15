"""Shared OpenAI compatibility helpers.

Extracts patterns that were previously duplicated across ``planner.py`` and
``task_classifier.py``.  Covers two common ``response_format={"type": "json_object"}``
issues on compatible endpoints:

1. User/input messages must mention ``json`` (handled by
   :func:`ensure_json_object_user_message`).
2. Some models reject ``json_object`` entirely (detected by
   :func:`json_object_unsupported` so callers can retry without JSON mode).
"""

from __future__ import annotations

from typing import Any

_JSON_OBJECT_USER_HINT = "Please return a valid JSON object."

_UNSUPPORTED_MARKERS = (
    "unsupported",
    "not support",
    "not supported",
    "does not support",
    "not available",
    "unavailable",
    "invalid parameter",
    "invalid request",
    "unrecognized",
    "unknown parameter",
)

_TEXT_CONTENT_KEYS = ("text", "input_text")


def _content_has_json(content: Any) -> bool:
    """Return True when *content* already mentions ``json`` (case-insensitive).

    Supports plain ``str`` user content and multimodal ``list`` parts.  For dict
    parts, checks common text-bearing keys (``text``, ``input_text``).
    """
    if isinstance(content, str):
        return "json" in content.lower()
    if isinstance(content, list):
        for part in content:
            if isinstance(part, str) and "json" in part.lower():
                return True
            if isinstance(part, dict):
                for key in _TEXT_CONTENT_KEYS:
                    text = part.get(key)
                    if isinstance(text, str) and "json" in text.lower():
                        return True
        return False
    return False


def _append_json_hint_to_text(text: str) -> str:
    separator = "\n\n" if text else ""
    return f"{text}{separator}{_JSON_OBJECT_USER_HINT}"


def _append_json_hint_to_multimodal(content: list[Any]) -> list[Any]:
    next_content = list(content)
    for j in range(len(next_content) - 1, -1, -1):
        part = next_content[j]
        if not isinstance(part, dict):
            continue
        for key in _TEXT_CONTENT_KEYS:
            text = part.get(key)
            if isinstance(text, str):
                next_part = dict(part)
                next_part[key] = _append_json_hint_to_text(text)
                next_content[j] = next_part
                return next_content
    next_content.append({"type": "text", "text": _JSON_OBJECT_USER_HINT})
    return next_content


def ensure_json_object_user_message(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return a shallow-copied message list with a JSON keyword in the last user turn.

    Some OpenAI-compatible endpoints require ``response_format={"type": "json_object"}``
    to be paired with a user/input message that mentions ``json`` (case-insensitive).
    System prompts in this project already request JSON; this helper guarantees the
    **last** user message satisfies that constraint.

    Behavior:
    - Non-``dict`` entries in *messages* are dropped (API payloads must be dicts).
    - Each retained message is shallow-copied; nested ``content`` is copied only when
      modified.
    - If the **last** user message already contains ``json``, the list is returned
      unchanged aside from the shallow dict copies.
    - Otherwise the hint ``"Please return a valid JSON object."`` is appended to the
      last user ``str``/multimodal text part, or replaces non-text ``content`` values
      such as ``None``.
    - When no user message exists, a synthetic user turn with the hint is appended.

    Args:
        messages: Chat completion messages (OpenAI-style dicts).

    Returns:
        A new list safe to pass alongside ``response_format={"type": "json_object"}``.
    """
    copied = [dict(message) for message in messages if isinstance(message, dict)]
    user_indexes = [i for i, message in enumerate(copied) if message.get("role") == "user"]

    if not user_indexes:
        copied.append({"role": "user", "content": _JSON_OBJECT_USER_HINT})
        return copied

    last_user_idx = user_indexes[-1]
    if _content_has_json(copied[last_user_idx].get("content")):
        return copied

    content = copied[last_user_idx].get("content")
    if isinstance(content, str):
        copied[last_user_idx]["content"] = _append_json_hint_to_text(content)
        return copied

    if isinstance(content, list):
        copied[last_user_idx]["content"] = _append_json_hint_to_multimodal(content)
        return copied

    copied[last_user_idx]["content"] = _JSON_OBJECT_USER_HINT
    return copied


def json_object_requires_json_keyword(err: Exception) -> bool:
    """Return True when *err* is the "user message must mention json" validation error.

    This error is prevented by :func:`ensure_json_object_user_message` and must **not**
    be treated as "endpoint unsupported" (see :func:`json_object_unsupported`).

    Matches OpenAI-style messages such as::

        Response input messages must contain the word 'json' in some form
        to use 'response.format' of type 'json_object'.
    """
    low = str(err).lower()
    return (
        "must contain the word" in low
        and "json" in low
        and ("json_object" in low or "response_format" in low or "format" in low)
    )


def json_object_unsupported(err: Exception) -> bool:
    """Return True if *err* indicates the endpoint rejects ``response_format=json_object``.

    Callers can retry the same prompt without ``response_format`` when this returns
    True.  Errors that :func:`json_object_requires_json_keyword` would match are
    excluded so callers do not silently drop JSON mode when a hint would suffice.

    Detection requires the error text to mention ``response_format`` or
    ``json_object`` **and** contain a known "unsupported/invalid" marker.
    """
    if json_object_requires_json_keyword(err):
        return False
    low = str(err).lower()
    if "response_format" not in low and "json_object" not in low:
        return False
    return any(marker in low for marker in _UNSUPPORTED_MARKERS)


__all__ = [
    "ensure_json_object_user_message",
    "json_object_requires_json_keyword",
    "json_object_unsupported",
]
