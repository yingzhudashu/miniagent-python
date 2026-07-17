"""流式思考段首可选前缀：段界跨 chunk 时仍正确（默认无缩进）。"""

from __future__ import annotations

from miniagent.assistant.engine.thinking import indent_stream_thinking_suffix


def test_indent_default_no_paragraph_prefix() -> None:
    full = "First line\nstill first paragraph\n\nSecond paragraph"
    assert indent_stream_thinking_suffix(full, 0) == full


def test_indent_incremental_paragraph_boundary_default() -> None:
    """stream_printed 使用源码长度（非 ANSI 长度）：\\n\\n 在上一段末尾时下一段仍正确拼接。"""
    acc1 = "A\n\n"
    assert indent_stream_thinking_suffix(acc1, 0) == "A\n\n"
    full = "A\n\nB"
    assert indent_stream_thinking_suffix(full, len(acc1)) == "B"


def test_indent_full_body_matches_incremental_parts_default() -> None:
    full = "A\n\nB"
    assert indent_stream_thinking_suffix(full, 0) == full
    assert indent_stream_thinking_suffix(full[: len("A\n\n")], 0) == "A\n\n"
    assert indent_stream_thinking_suffix(full, len("A\n\n")) == "B"


def test_indent_no_paragraph_break_mid_word() -> None:
    assert indent_stream_thinking_suffix("hello wo", 0) == "hello wo"
    assert indent_stream_thinking_suffix("hello world", len("hello wo")) == "rld"


def test_indent_empty_suffix() -> None:
    assert indent_stream_thinking_suffix("x", 1) == ""
    assert indent_stream_thinking_suffix("", 0) == ""


def test_indent_explicit_four_spaces_paragraph_starts() -> None:
    """传入 indent 时仍可为段首加前缀（供需要视觉缩进的调用方）。"""
    full = "First line\nstill first paragraph\n\nSecond paragraph"
    assert indent_stream_thinking_suffix(full, 0, indent="    ") == (
        "    First line\nstill first paragraph\n\n    Second paragraph"
    )
    acc1 = "A\n\n"
    assert indent_stream_thinking_suffix(acc1, 0, indent="    ") == "    A\n\n"
    full2 = "A\n\nB"
    assert indent_stream_thinking_suffix(full2, len(acc1), indent="    ") == "    B"
