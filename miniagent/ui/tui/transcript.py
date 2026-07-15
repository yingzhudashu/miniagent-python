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

import re
from collections import deque
from collections.abc import Iterable, Iterator
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
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


class TranscriptBuffer:
    """维护可见字符预算的 transcript 双端缓冲区。

    所有增删改操作都在同一对象内更新 ``total_len``，从而消除 TUI 多个闭包
    分别维护 deque 与长度计数器时产生漂移的风险。缓冲区超过预算后从最旧端
    裁剪，但至少保留 ``min_fragments`` 个片段，保证当前渲染块不会被清空。

    该对象只在 TUI 事件循环线程内使用，不提供跨线程同步。
    """

    def __init__(self, max_chars: int, *, min_fragments: int = 16) -> None:
        """创建字符预算和最小片段数均已归一化的空缓冲区。"""
        self.max_chars = max(0, int(max_chars))
        self.min_fragments = max(0, int(min_fragments))
        self._items: deque[Any] = deque()
        self.total_len = 0

    def __bool__(self) -> bool:
        """返回缓冲区是否包含片段。"""
        return bool(self._items)

    def __len__(self) -> int:
        """返回片段数量。"""
        return len(self._items)

    def __iter__(self) -> Iterator[Any]:
        """按显示顺序迭代片段。"""
        return iter(self._items)

    def __getitem__(self, index: int) -> Any:
        """按索引读取片段。"""
        return self._items[index]

    def __setitem__(self, index: int, fragment: Any) -> None:
        """替换片段并以可见字符差值更新计数器。"""
        previous = self._items[index]
        self._items[index] = fragment
        self.total_len = max(
            0,
            self.total_len - transcript_fragment_len(previous) + transcript_fragment_len(fragment),
        )
        self.trim()

    def append(self, fragment: Any) -> None:
        """在尾部追加片段并执行预算裁剪。"""
        self._items.append(fragment)
        self.total_len += transcript_fragment_len(fragment)
        self.trim()

    def prepend(self, fragment: Any) -> None:
        """在头部插入片段并执行预算裁剪。"""
        self._items.appendleft(fragment)
        self.total_len += transcript_fragment_len(fragment)
        self.trim()

    def extend(self, fragments: Iterable[Any]) -> None:
        """在尾部批量追加片段，并在批次完成后裁剪。"""
        for fragment in fragments:
            self._items.append(fragment)
            self.total_len += transcript_fragment_len(fragment)
        self.trim()

    def popleft(self) -> Any:
        """移除最旧片段并返回它。"""
        fragment = self._items.popleft()
        self.total_len = max(0, self.total_len - transcript_fragment_len(fragment))
        return fragment

    def pop(self) -> Any:
        """移除最新片段并返回它。"""
        fragment = self._items.pop()
        self.total_len = max(0, self.total_len - transcript_fragment_len(fragment))
        return fragment

    def clear(self) -> None:
        """清空片段与累计字符计数。"""
        self._items.clear()
        self.total_len = 0

    def trim(self) -> None:
        """从最旧端裁剪到字符预算内，同时遵守最小片段保留量。"""
        while self.total_len > self.max_chars and len(self._items) > self.min_fragments:
            self.popleft()


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
            return _ANSI_ESCAPE.sub("", fragment.value) or ""
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
    "TranscriptBuffer",
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
