"""Tests for ``miniagent.assistant.engine.cli_format``."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from miniagent.assistant.engine.cli_format import (
    format_cli_reply_block,
    format_cli_user_block,
    get_cli_format_widths,
)


def _collect_append(
    calls: list[tuple[str, str]],
) -> Any:
    def append(style: str, text: str) -> None:
        calls.append((style, text))

    return append


def test_format_cli_user_block_skips_empty() -> None:
    calls: list[tuple[str, str]] = []
    stick = [False]
    format_cli_user_block(_collect_append(calls), "", stick)
    format_cli_user_block(None, "hello", stick)
    assert calls == []
    assert stick == [False]


def test_format_cli_user_block_with_channel_and_width() -> None:
    calls: list[tuple[str, str]] = []
    stick = [False]
    format_cli_user_block(
        _collect_append(calls),
        "你好\n世界",
        stick,
        channel_label="飞书私聊",
        render_width=60,
    )
    assert stick == [True]
    styles = [s for s, _ in calls]
    assert "class:cli-user-title" in styles
    texts = "".join(t for _, t in calls)
    assert "You · [飞书私聊]" in texts
    assert "你好" in texts
    assert "世界" in texts
    assert "═" * 60 in texts


def test_format_cli_reply_block_plain_fallback() -> None:
    calls: list[tuple[str, str]] = []
    format_cli_reply_block(
        _collect_append(calls),
        None,
        "plain line",
        render_width=50,
        markdown_width=46,
    )
    texts = "".join(t for _, t in calls)
    assert "Assistant" in texts
    assert "plain line" in texts
    assert "═" * 50 in texts


def test_format_cli_reply_block_uses_safe_ansi_fragments_on_invalid_styles() -> None:
    calls: list[tuple[str, str]] = []
    ansi_calls: list[Any] = []

    def append_ansi(obj: Any) -> None:
        ansi_calls.append(obj)

    with patch(
        "miniagent.assistant.engine.markdown_cli.render_markdown_to_ansi",
        return_value="\x1b[31mok\x1b[0m\n",
    ), patch(
        "miniagent.assistant.engine.cli_format.safe_ansi_fragments",
        return_value=[("", "ok\n")],
    ), patch(
        "prompt_toolkit.formatted_text.to_formatted_text",
        return_value=[("badstyle!", "ok")],
    ):
        format_cli_reply_block(
            _collect_append(calls),
            append_ansi,
            "**bold**",
            render_width=40,
            markdown_width=36,
        )

    assert ansi_calls == []
    assert any(t == "ok\n" for _, t in calls)


def test_get_cli_format_widths_from_state() -> None:
    state = {
        "cli_render_width": lambda: 100,
        "cli_markdown_width": lambda: 96,
    }
    assert get_cli_format_widths(state) == (100, 96)
    assert get_cli_format_widths({}) == (None, None)
    assert get_cli_format_widths(None) == (None, None)


def test_format_cli_reply_block_skips_empty() -> None:
    calls: list[tuple[str, str]] = []
    format_cli_reply_block(_collect_append(calls), None, "")
    assert calls == []
