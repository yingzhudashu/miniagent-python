"""Pure transcript formatting and history-loading helpers for full-screen CLI.

The full-screen CLI stores transcript lines in a deque and prepends older history
at the top. This module keeps that rendering model explicit and testable:

- **History lazy-load**: ``start_idx`` counts from the newest message backward.
  ``loaded_end`` is how many tail messages are already represented in the pane.
  ``load_session_history_range`` may add one earlier user message when a slice
  starts with assistant; use ``len(messages)`` (not the requested ``count``) when
  updating ``loaded_end``.
- **Left-prepend order**: ``messages_for_prepend`` / ``lines_for_prepend`` reverse
  batches so repeated ``insert(0)`` yields old-to-new display order.
- **Fragment text/length**: entries are ``(style_cls, text)`` tuples or
  prompt_toolkit ``ANSI`` objects. Plain text and visible length both strip ANSI
  escape sequences for copy/selection and trim accounting.
"""

from __future__ import annotations

from typing import Any

# Style class paired with ``history_load_hint`` for the top-of-transcript hint.
HISTORY_HINT_STYLE = "class:cli-hint"

_VALID_PT_STYLE_PREFIXES = ("ansi", "#", "class:", "bg:", "fg:", "noinherit")
_VALID_PT_ANSI_COLOR_NAMES = frozenset({
    "ansidefault", "ansiblack", "ansired", "ansigreen", "ansiyellow",
    "ansiblue", "ansimagenta", "ansicyan", "ansiwhite",
    "ansibrightblack", "ansibrightred", "ansibrightgreen", "ansibrightyellow",
    "ansibrightblue", "ansibrightmagenta", "ansibrightcyan", "ansibrightwhite",
})


def is_valid_pt_style(style: str) -> bool:
    """判断 prompt_toolkit 样式字符串是否合法（避免 emoji 等触发解析错误）。"""
    if not style:
        return True
    if style in _VALID_PT_ANSI_COLOR_NAMES:
        return True
    return any(style.startswith(prefix) for prefix in _VALID_PT_STYLE_PREFIXES)


def safe_ansi_fragments(ansi_body: str) -> list[tuple[str, str]]:
    """将 ANSI 文本解析为已过滤非法样式的 ``(style, text)`` 片段列表。"""
    from prompt_toolkit.formatted_text import ANSI, to_formatted_text

    ansi_obj = ANSI(ansi_body)
    safe_fragments: list[tuple[str, str]] = []
    for style, text in to_formatted_text(ansi_obj):
        if is_valid_pt_style(style):
            safe_fragments.append((style, text))
        else:
            safe_fragments.append(("", text))
    return safe_fragments


def history_load_hint(remaining: int) -> str:
    """Return the top-of-transcript lazy-load hint for remaining older messages.

    Args:
        remaining: Count of older messages not yet shown (non-positive → empty).
    """
    if remaining <= 0:
        return ""
    return f"\n[↑ 向上滚动加载更多历史 · 还有 {remaining} 条]\n"


def history_loaded_end(start_idx: int, loaded_count: int, total: int) -> int:
    """Return tail-relative count represented after a history range load.

    ``load_session_history_range`` can include one extra earlier user message to
    keep a user/assistant turn intact.  The CLI tracks how many messages from
    the newest tail are already displayed, so the actual displayed count must be
    used and clamped to the known total.

    Args:
        start_idx: Tail-relative start index passed to ``load_session_history_range``.
        loaded_count: Actual number of messages returned (``len(messages)``).
        total: Total messages in the session history.

    Returns:
        Updated ``loaded_end``, clamped to ``[0, total]``.
    """
    return min(max(0, total), max(0, start_idx) + max(0, loaded_count))


def history_remaining(total: int, loaded_end: int) -> int:
    """Return how many older messages remain outside the current transcript.

    Args:
        total: Total messages in session history.
        loaded_end: Tail-relative count already represented in the pane.
    """
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


def transcript_fragment_text(fragment: Any) -> str:
    """Return plain text for one transcript fragment.

    Supports ``(style_cls, text)`` tuples and prompt_toolkit ``ANSI`` objects.
    ANSI escape sequences are stripped. Unknown fragment types return ``""``.

    Args:
        fragment: One deque entry from the CLI transcript store.
    """
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


def transcript_fragment_len(fragment: Any) -> int:
    """Return visible character length for one transcript fragment.

    Uses the same plain-text extraction as ``transcript_fragment_text`` so trim
    accounting matches copy/selection (ANSI escapes are not counted).
    """
    return len(transcript_fragment_text(fragment))


def transcript_plain(fragments: list[Any]) -> str:
    """Return plain text for a sequence of stored transcript fragments.

    Args:
        fragments: Ordered transcript deque contents (oldest fragment first).
    """
    return "".join(transcript_fragment_text(fragment) for fragment in fragments)


def markdown_render_width(viewport_cols: int, margin: int) -> int:
    """Return the Markdown render width derived from viewport width and margin.

    Args:
        viewport_cols: Usable column count (scrollbar already deducted).
        margin: Extra columns to reserve beside body text.

    Returns:
        ``max(40, viewport_cols - margin)``.
    """
    return max(40, viewport_cols - int(margin))


def rule_line_width(viewport_cols: int) -> int:
    """Return the full-width separator line length for the transcript viewport.

    Args:
        viewport_cols: Usable column count (scrollbar already deducted).

    Returns:
        ``max(40, viewport_cols)``.
    """
    return max(40, viewport_cols)


__all__ = [
    "HISTORY_HINT_STYLE",
    "history_all_loaded",
    "history_load_hint",
    "history_loaded_end",
    "history_remaining",
    "is_valid_pt_style",
    "lines_for_prepend",
    "markdown_render_width",
    "messages_for_prepend",
    "rule_line_width",
    "safe_ansi_fragments",
    "transcript_fragment_len",
    "transcript_fragment_text",
    "transcript_plain",
]
