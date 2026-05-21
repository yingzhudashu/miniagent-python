"""Shared OpenAI compatibility helpers.

Extracts patterns that were previously duplicated across planner.py and task_classifier.py.
"""

from __future__ import annotations

try:
    from openai import BadRequestError as _OpenAIBadRequestError
except ImportError:
    _OpenAIBadRequestError = None  # type: ignore[misc, assignment]


def json_object_unsupported(err: Exception) -> bool:
    """Return True if *err* indicates the endpoint doesn't support ``response_format=json_object``.

    Callers can use this to decide whether to retry without JSON mode.
    """
    if _OpenAIBadRequestError is not None and isinstance(err, _OpenAIBadRequestError):
        return True
    low = str(err).lower()
    return "response_format" in low or "json_object" in low


__all__ = ["json_object_unsupported"]
