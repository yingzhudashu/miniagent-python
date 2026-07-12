"""Pure transcript helper tests for the full-screen CLI."""

from __future__ import annotations

from miniagent.engine.cli_transcript import (
    HISTORY_HINT_STYLE,
    TranscriptBuffer,
    history_all_loaded,
    history_load_hint,
    history_loaded_end,
    history_remaining,
    lines_for_prepend,
    markdown_render_width,
    messages_for_prepend,
    rule_line_width,
    transcript_fragment_len,
    transcript_fragment_text,
    transcript_plain,
)


def test_transcript_buffer_keeps_length_in_sync_across_mutations() -> None:
    buffer = TranscriptBuffer(100, min_fragments=0)
    buffer.append(("class:a", "abc"))
    buffer.prepend(("class:b", "xy"))
    buffer.extend([("class:c", "1234"), ("class:d", "z")])
    assert buffer.total_len == 10
    assert list(buffer) == [
        ("class:b", "xy"),
        ("class:a", "abc"),
        ("class:c", "1234"),
        ("class:d", "z"),
    ]

    buffer[-1] = ("class:d", "long")
    assert buffer.total_len == 13
    buffer.pop()
    buffer.popleft()
    assert buffer.total_len == 7
    buffer.clear()
    assert buffer.total_len == 0
    assert not buffer


def test_transcript_buffer_trims_oldest_fragments_but_keeps_minimum() -> None:
    buffer = TranscriptBuffer(5, min_fragments=2)
    buffer.extend([("", "aaa"), ("", "bbb"), ("", "ccc")])
    assert list(buffer) == [("", "bbb"), ("", "ccc")]
    assert buffer.total_len == 6

    buffer.max_chars = 10
    buffer.append(("", "d"))
    assert buffer.total_len == 7


def test_history_load_hint_reports_remaining_older_messages() -> None:
    """Partial transcript history shows a stable top-of-pane lazy-load hint."""
    assert HISTORY_HINT_STYLE == "class:cli-hint"
    assert history_load_hint(0) == ""
    assert history_load_hint(-1) == ""
    assert history_load_hint(3) == "\n[↑ 向上滚动加载更多历史 · 还有 3 条]\n"


def test_history_loaded_end_counts_range_expansion_and_clamps_total() -> None:
    """Loaded range accounting includes extra messages added to preserve turns."""
    assert history_loaded_end(0, 5, 20) == 5
    assert history_loaded_end(5, 4, 20) == 9
    assert history_loaded_end(18, 4, 20) == 20
    assert history_loaded_end(-3, -2, 20) == 0


def test_history_remaining_and_all_loaded_use_tail_relative_count() -> None:
    """Remaining-history helpers keep lazy-load state non-negative and explicit."""
    assert history_remaining(20, 5) == 15
    assert history_remaining(20, 25) == 0
    assert not history_all_loaded(20, 19)
    assert history_all_loaded(20, 20)
    assert history_all_loaded(0, 0)


def test_messages_for_prepend_reverses_batch_for_left_insertion() -> None:
    """Repeated left-prepend rendering needs the newest batch item inserted first."""
    messages = [
        {"role": "user", "content": "old"},
        {"role": "assistant", "content": "middle"},
        {"role": "user", "content": "new"},
    ]

    assert messages_for_prepend(messages) == [
        {"role": "user", "content": "new"},
        {"role": "assistant", "content": "middle"},
        {"role": "user", "content": "old"},
    ]
    assert messages_for_prepend([]) == []


def test_lines_for_prepend_keeps_multiline_text_in_display_order() -> None:
    """Repeated left-prepend rendering reverses input lines before insertion."""
    assert lines_for_prepend("a\nb\nc") == ["c", "b", "a"]
    assert lines_for_prepend("") == [""]


def test_transcript_fragment_len_counts_tuple_text_without_preview_truncation() -> None:
    """Transcript length accounting does not truncate long assistant answers."""
    long_text = "answer" * 1000
    assert transcript_fragment_len(("class:cli-assistant-body", long_text)) == len(long_text)
    assert transcript_fragment_len(("style",)) == 0
    assert transcript_fragment_len(object()) == 0


def test_transcript_fragment_len_strips_ansi_for_visible_length() -> None:
    """Visible length for ANSI fragments matches stripped plain text."""
    from prompt_toolkit.formatted_text.ansi import ANSI

    ansi = ANSI("\x1b[32mok\x1b[0m")
    assert transcript_fragment_len(ansi) == 2
    assert transcript_fragment_len(ansi) == len(transcript_fragment_text(ansi))


def test_transcript_fragment_text_reads_tuple_and_ansi_text() -> None:
    """Plain-text extraction supports tuple fragments and prompt-toolkit ANSI."""
    from prompt_toolkit.formatted_text.ansi import ANSI

    assert transcript_fragment_text(("class:cli-default", "hello")) == "hello"
    assert transcript_fragment_text(("class:cli-default", None)) == ""
    assert transcript_fragment_text(ANSI("\x1b[32mok\x1b[0m")) == "ok"
    assert transcript_fragment_text(object()) == ""


def test_transcript_plain_joins_stored_fragments() -> None:
    """Stored transcript fragments are joined as plain text for clipboard export."""
    from prompt_toolkit.formatted_text.ansi import ANSI

    fragments = [
        ("style", "hello "),
        ANSI("\x1b[32mworld\x1b[0m"),
        object(),
        ("style", "\n"),
    ]

    assert transcript_plain(fragments) == "hello world\n"


def test_markdown_and_rule_width_helpers_keep_existing_minimums() -> None:
    """Width helpers keep transcript rendering readable in narrow terminals."""
    assert markdown_render_width(120, 2) == 118
    assert markdown_render_width(20, 2) == 40
    assert rule_line_width(120) == 120
    assert rule_line_width(20) == 40
