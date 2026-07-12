"""CLI transcript 滚动相关源码回归（自 ``scripts/verify_scroll_fix.py`` 迁移）。"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CLI_TUI_PY = _REPO_ROOT / "miniagent" / "engine" / "cli_tui.py"


def _tui_source() -> str:
    return _CLI_TUI_PY.read_text(encoding="utf-8")


def _ctrl_l_handler_block(source: str) -> str:
    start = source.index('@kb.add("c-l", filter=has_focus(input_buffer))')
    end = source.index('@kb.add("c-t", filter=has_focus(input_buffer))', start)
    return source[start:end]


def _reset_and_reload_transcript_block(source: str) -> str:
    start = source.index("def _reset_and_reload_transcript(")
    end = source.index("def _trigger_lazy_load_more_history(", start)
    return source[start:end]


def _load_initial_history_block(source: str) -> str:
    start = source.index("def _load_initial_history_to_transcript(")
    end = source.index("def _reset_and_reload_transcript(", start)
    return source[start:end]


def _lazy_load_history_block(source: str) -> str:
    start = source.index("def _trigger_lazy_load_more_history(")
    end = source.index("def _attach_md_source(", start)
    return source[start:end]


def test_pageup_pagedown_use_apply_transcript_scroll() -> None:
    source = _tui_source()
    assert re.search(
        r'_apply_transcript_scroll\(-_scroll_step\(\), "pageup"\)',
        source,
    )
    assert re.search(
        r'_apply_transcript_scroll\(_scroll_step\(\), "pagedown"\)',
        source,
    )


def test_ctrl_l_uses_reset_and_reload_transcript() -> None:
    block = _ctrl_l_handler_block(_tui_source())
    assert "_reset_and_reload_transcript(reset_scroll_to_top=True)" in block


def test_ctrl_l_no_invalid_output_scroll_assignment() -> None:
    source = _tui_source()
    assert not re.search(r"output_scroll\.horizontal_scroll\s*=\s*0", source)


def test_scrollbar_style_high_contrast() -> None:
    source = _tui_source()
    assert re.search(r'"scrollbar\.button":\s*"bg:ansibrightcyan', source)


def test_reset_and_reload_transcript_resets_scroll_when_requested() -> None:
    block = _reset_and_reload_transcript_block(_tui_source())
    assert "if reset_scroll_to_top:" in block
    assert re.search(r"sp\s*=\s*_sp\(\)", block)
    assert "sp.vertical_scroll = 0" in block
    assert "_reset_horizontal_scroll()" in block


def test_transcript_uses_explicit_character_limit_instead_of_fixed_deque_maxlen() -> None:
    source = _tui_source()
    assert "_transcript: deque[Any] = deque()" in source
    assert "deque(maxlen=5000)" not in source


def test_transcript_prepend_handles_unbounded_deque_and_trims_by_chars() -> None:
    source = _tui_source()
    start = source.index("def _transcript_prepend(")
    end = source.index("def _render_history_message_to_transcript(", start)
    block = source[start:end]

    assert "_transcript.insert(0," in block
    assert "_trim_transcript()" in block


def test_initial_history_hint_is_prepended_above_loaded_messages() -> None:
    block = _load_initial_history_block(_tui_source())
    assert "_transcript_prepend(HISTORY_HINT_STYLE, history_load_hint(remaining))" in block
    assert "history_remaining(total, _history_loaded_range[\"loaded_end\"])" in block


def test_lazy_history_prepend_reverses_loaded_batch_before_rendering() -> None:
    block = _lazy_load_history_block(_tui_source())
    assert "for msg in messages_for_prepend(messages):" in block
    assert "history_loaded_end(" in block
