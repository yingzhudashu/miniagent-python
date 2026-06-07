"""Pure transcript formatting and history-loading helpers for full-screen CLI."""

from __future__ import annotations

from typing import Any

HISTORY_HINT_STYLE = "class:cli-hint"


def history_load_hint(remaining: int) -> str:
    """Return the top-of-transcript lazy-load hint for remaining older messages."""
    if remaining <= 0:
        return ""
    return f"\n[↑ 向上滚动加载更多历史 · 还有 {remaining} 条]\n"


def history_loaded_end(start_idx: int, loaded_count: int, total: int) -> int:
    """Return tail-relative count represented after a history range load.

    ``load_session_history_range`` can include one extra earlier user message to
    keep a user/assistant turn intact.  The CLI tracks how many messages from
    the newest tail are already displayed, so the actual displayed count must be
    used and clamped to the known total.
    """
    return min(max(0, total), max(0, start_idx) + max(0, loaded_count))


def history_remaining(total: int, loaded_end: int) -> int:
    """Return how many older messages remain outside the current transcript."""
    return max(0, total - loaded_end)


def history_all_loaded(total: int, loaded_end: int) -> bool:
    """Return whether all known session messages are represented in transcript."""
    return history_remaining(total, loaded_end) == 0


def messages_for_prepend(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return messages in the order required for repeated left-prepend rendering."""
    return list(reversed(messages))


def lines_for_prepend(text: str) -> list[str]:
    """Return text lines in the order required for repeated left-prepend rendering."""
    return list(reversed((text or "").splitlines() or [""]))


def transcript_fragment_len(fragment: Any) -> int:
    """Return the approximate visible text length for one transcript fragment."""
    if isinstance(fragment, tuple) and len(fragment) >= 2:
        return len(fragment[1] or "")
    try:
        return len(fragment.value)
    except Exception:
        return 0


def transcript_fragment_text(fragment: Any) -> str:
    """Return plain text for one transcript fragment."""
    if isinstance(fragment, tuple) and len(fragment) >= 2:
        return fragment[1] or ""

    try:
        from prompt_toolkit.formatted_text.ansi import ANSI as PTANSI

        if isinstance(fragment, PTANSI):
            from miniagent.engine.markdown_cli import strip_ansi

            return strip_ansi(fragment.value) or ""
    except Exception:
        return ""

    return ""


def transcript_plain(fragments: list[Any]) -> str:
    """Return plain text for a sequence of stored transcript fragments."""
    return "".join(transcript_fragment_text(fragment) for fragment in fragments)


def markdown_render_width(viewport_cols: int, margin: int) -> int:
    """Return the Markdown render width derived from viewport width and margin."""
    return max(40, viewport_cols - int(margin))


def rule_line_width(viewport_cols: int) -> int:
    """Return the full-width separator line length for the transcript viewport."""
    return max(40, viewport_cols)


__all__ = [
    "HISTORY_HINT_STYLE",
    "history_all_loaded",
    "history_load_hint",
    "history_loaded_end",
    "history_remaining",
    "lines_for_prepend",
    "markdown_render_width",
    "messages_for_prepend",
    "rule_line_width",
    "transcript_fragment_len",
    "transcript_fragment_text",
    "transcript_plain",
]
