"""TUI transcript 控制器的历史、选择和宽度回归测试。"""

from __future__ import annotations

from types import SimpleNamespace

from prompt_toolkit.formatted_text.ansi import ANSI

from miniagent.engine.cli_transcript import TranscriptBuffer
from miniagent.engine.cli_tui_transcript_ops import create_transcript_operations


class _History:
    def load_session_history_range(self, _session_id, *, start_idx: int, count: int):
        messages = [
            {"role": "user", "content": "question"},
            {"role": "assistant", "content": "answer"},
            {"role": "tool", "content": "tool-output", "name": "read_file"},
            {"role": "system", "content": "system-note"},
        ]
        return messages[start_idx : start_idx + count], len(messages)


def _make_operations(
    monkeypatch,
    *,
    history=None,
    is_valid_pt_style=lambda _style: True,
    safe_ansi=lambda value: [("", str(value))],
    viewport_cols=lambda: 40,
):
    transcript = TranscriptBuffer(10_000)
    history_state = {
        "total_messages": 0,
        "loaded_start": 0,
        "loaded_end": 0,
        "batch_size": 2,
        "all_loaded": False,
        "loading": False,
    }
    stick_bottom = [False]
    copy_mode = [False]
    mouse_down = [False]
    selection_start = [None]
    selection_end = [None]
    selection_text = [""]
    scroll = SimpleNamespace(vertical_scroll=7)
    invalidations: list[bool] = []
    resets: list[bool] = []
    snaps: list[bool] = []

    def fake_get_app() -> SimpleNamespace:
        return SimpleNamespace(is_running=True, invalidate=lambda: invalidations.append(True))
    monkeypatch.setattr("miniagent.engine.cli_tui_transcript_ops.get_app", fake_get_app)
    monkeypatch.setattr("prompt_toolkit.application.get_app", fake_get_app)

    def append(style, text="", **_kwargs):
        transcript.append((style, text))

    operations = create_transcript_operations(
        state={"session_manager": history or _History(), "active_session_id": "default"},
        initial_history_count=2,
        history_loaded_range=history_state,
        transcript=transcript,
        stick_bottom=stick_bottom,
        last_md_width=[0],
        copy_mode_active=copy_mode,
        copy_mode_mouse_down=mouse_down,
        selection_start=selection_start,
        selection_end=selection_end,
        selection_text=selection_text,
        is_valid_pt_style=is_valid_pt_style,
        safe_ansi=safe_ansi,
        sp=lambda: scroll,
        viewport_cols=viewport_cols,
        append_transcript=append,
        markdown_render_width=lambda: 38,
        cli_block_user=lambda text: append("class:cli-user-body", text),
        cli_block_reply=lambda text: append("class:cli-assistant-body", text),
        should_wrap_lines=lambda: True,
        reset_horizontal_scroll=lambda: resets.append(True),
        snap_output_bottom=lambda: snaps.append(True),
    )
    return SimpleNamespace(
        operations=operations,
        transcript=transcript,
        history_state=history_state,
        stick_bottom=stick_bottom,
        copy_mode=copy_mode,
        mouse_down=mouse_down,
        selection_start=selection_start,
        selection_end=selection_end,
        selection_text=selection_text,
        scroll=scroll,
        invalidations=invalidations,
        resets=resets,
        snaps=snaps,
    )


def test_history_load_reset_and_lazy_pagination(monkeypatch) -> None:
    state = _make_operations(monkeypatch)
    ops = state.operations
    ops.load_initial_history()
    assert state.history_state["loaded_end"] == 2
    assert not state.history_state["all_loaded"]
    assert any("question" in str(item) for item in state.transcript)

    ops.trigger_lazy_load_more_history()
    assert state.history_state["loaded_end"] == 4
    assert state.history_state["all_loaded"]
    assert state.invalidations

    ops.reset_and_reload_transcript(reset_scroll_to_top=True)
    assert state.scroll.vertical_scroll == 0
    assert state.resets and state.snaps
    assert state.stick_bottom[0]


def test_selection_copy_mode_and_flatten(monkeypatch) -> None:
    state = _make_operations(monkeypatch)
    ops = state.operations
    state.transcript.extend(
        [
            ("class:cli-border", "═" * 80 + "\n"),
            ("class:cli-default", "hello"),
            ("class:cli-default", " world"),
        ]
    )
    state.selection_start[0] = (1, 1)
    state.selection_end[0] = (2, 3)
    assert ops.extract_selection_text() == "ello wo"
    state.selection_text[0] = ops.extract_selection_text()

    ops.toggle_copy_mode()
    assert state.copy_mode[0]
    highlighted = ops.apply_selection_highlight(1, "hello")
    assert any(style == "class:cli-selection" for style, _ in highlighted)
    flattened = ops.flatten_transcript_for_pt()
    assert flattened

    ops.clear_selection()
    assert state.selection_start[0] is None
    assert state.selection_text[0] == ""
    assert not state.mouse_down[0]
    ops.toggle_copy_mode()
    assert not state.copy_mode[0]


def test_fragment_helpers_and_missing_history_degrade(monkeypatch) -> None:
    state = _make_operations(monkeypatch)
    ops = state.operations
    assert ops.transcript_fragment_len(("", "abc")) == 3
    state.transcript.append(("", "abc"))
    assert ops.get_transcript_fragment_text(0) == "abc"
    assert ops.get_transcript_char_count(0) == 3
    assert ops.get_transcript_fragment_text(99) == ""
    ops.transcript_prepend("", "top")
    assert "top" in ops.get_transcript_fragment_text(0)
    ops.trim_transcript()
    ops.recheck_md_width()

    empty = create_transcript_operations(
        state={},
        initial_history_count=1,
        history_loaded_range=state.history_state,
        transcript=state.transcript,
        stick_bottom=state.stick_bottom,
        last_md_width=[0],
        copy_mode_active=state.copy_mode,
        copy_mode_mouse_down=state.mouse_down,
        selection_start=state.selection_start,
        selection_end=state.selection_end,
        selection_text=state.selection_text,
        is_valid_pt_style=lambda _style: True,
        safe_ansi=lambda value: [("", str(value))],
        sp=lambda: None,
        viewport_cols=lambda: 80,
        append_transcript=lambda *_args, **_kwargs: None,
        markdown_render_width=lambda: 78,
        cli_block_user=lambda _text: None,
        cli_block_reply=lambda _text: None,
        should_wrap_lines=lambda: False,
        reset_horizontal_scroll=lambda: None,
        snap_output_bottom=lambda: None,
    )
    empty.load_initial_history()
    empty.trigger_lazy_load_more_history()


def test_render_history_roles_and_prepend_paths(monkeypatch) -> None:
    state = _make_operations(monkeypatch)
    ops = state.operations

    ops.render_history_message({"role": "user", "content": "old user"}, prepend=True)
    ops.render_history_message(
        {"role": "assistant", "content": "plain reply"},
        plain_text=True,
    )
    ops.render_history_message(
        {"role": "assistant", "content": "**old reply**"},
        prepend=True,
    )
    ops.render_history_message({"role": "thinking", "content": "step"})
    ops.render_history_message(
        {"role": "thinking", "content": "old step"},
        prepend=True,
    )
    before = len(state.transcript)
    ops.render_history_message({"role": "assistant", "content": ""})
    ops.render_history_message({"role": "unknown", "content": "ignored"})

    rendered = "".join(ops.get_transcript_fragment_text(i) for i in range(len(state.transcript)))
    assert "old user" in rendered
    assert "plain reply" in rendered
    assert "Assistant" in rendered
    assert "Thinking" in rendered
    assert len(state.transcript) == before


def test_render_history_prepend_falls_back_when_markdown_is_empty(monkeypatch) -> None:
    state = _make_operations(monkeypatch, safe_ansi=lambda _value: [])
    monkeypatch.setattr(
        "miniagent.engine.markdown_cli.render_markdown_to_ansi",
        lambda *_args, **_kwargs: "",
    )

    state.operations.render_history_message(
        {"role": "assistant", "content": "fallback body"},
        prepend=True,
    )

    assert any("fallback body" in str(item) for item in state.transcript)


def test_selection_reverse_multifragment_and_highlight_edges(monkeypatch) -> None:
    state = _make_operations(monkeypatch)
    ops = state.operations
    state.transcript.extend(
        [
            ("class:cli-default", "abc"),
            ("class:cli-default", "def"),
            ("class:cli-default", "ghi"),
        ]
    )
    state.selection_start[0] = (2, 2)
    state.selection_end[0] = (0, 1)

    assert ops.extract_selection_text() == "bcdefgh"
    assert ops.apply_selection_highlight(9, "outside") == [
        ("class:cli-default", "outside")
    ]
    assert ops.apply_selection_highlight(0, "abc") == [
        ("class:cli-default", "a"),
        ("class:cli-selection", "bc"),
    ]
    assert ops.apply_selection_highlight(1, "def") == [
        ("class:cli-selection", "def")
    ]
    assert ops.apply_selection_highlight(2, "ghi") == [
        ("class:cli-selection", "gh"),
        ("class:cli-default", "i"),
    ]

    state.selection_start[0] = (1, 1)
    state.selection_end[0] = (1, 2)
    assert ops.apply_selection_highlight(1, "def") == [
        ("class:cli-default", "d"),
        ("class:cli-selection", "e"),
        ("class:cli-default", "f"),
    ]

    state.selection_start[0] = None
    assert ops.extract_selection_text() == ""
    assert ops.apply_selection_highlight(0, "abc") == [
        ("class:cli-default", "abc")
    ]


def test_flatten_filters_styles_truncates_borders_and_expands_ansi(monkeypatch) -> None:
    state = _make_operations(
        monkeypatch,
        is_valid_pt_style=lambda style: style != "bad-style",
        viewport_cols=lambda: 10,
    )
    state.transcript.extend(
        [
            ("bad-style", "plain"),
            ("class:cli-border", "-" * 20 + "\n"),
            ANSI("\x1b[31mred\x1b[0m"),
            [("bad-style", "----\n")],
        ]
    )

    flattened = state.operations.flatten_transcript_for_pt()

    assert ("", "plain") in flattened
    assert any(text == "-----\n" for _style, text, *_ in flattened if isinstance(text, str))
    flattened_text = "".join(
        str(item[1]) for item in flattened if isinstance(item, tuple) and len(item) >= 2
    )
    assert "red" in flattened_text


def test_flatten_copy_mode_handles_ansi_and_non_tuple_fragments(monkeypatch) -> None:
    state = _make_operations(monkeypatch)
    state.transcript.extend(
        [
            ANSI("\x1b[32mansi\x1b[0m"),
            [("", "list text")],
        ]
    )
    state.copy_mode[0] = True
    state.selection_start[0] = (0, 1)
    state.selection_end[0] = (1, 4)

    flattened = state.operations.flatten_transcript_for_pt()

    assert any(item[0] == "class:cli-selection" for item in flattened)


def test_history_empty_exception_and_lazy_load_guards(monkeypatch) -> None:
    class EmptyHistory:
        def load_session_history_range(self, *_args, **_kwargs):
            return [], 0

    empty = _make_operations(monkeypatch, history=EmptyHistory())
    empty.operations.load_initial_history()
    assert empty.history_state["all_loaded"] is True
    empty.operations.trigger_lazy_load_more_history()

    class BrokenHistory:
        def load_session_history_range(self, *_args, **_kwargs):
            raise RuntimeError("broken history")

    broken = _make_operations(monkeypatch, history=BrokenHistory())
    broken.operations.load_initial_history()
    broken.history_state["loading"] = True
    broken.operations.trigger_lazy_load_more_history()
    assert broken.history_state["loading"] is True
