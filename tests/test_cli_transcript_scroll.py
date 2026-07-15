"""CLI transcript 滚动相关源码回归（自 ``scripts/verify_scroll_fix.py`` 迁移）。"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CLI_TUI_PY = _REPO_ROOT / "miniagent" / "assistant" / "engine" / "cli_tui.py"
_TRANSCRIPT_OPS_PY = (
    _REPO_ROOT / "miniagent" / "assistant" / "engine" / "cli_tui_transcript_ops.py"
)
_KEYBINDINGS_PY = (
    _REPO_ROOT / "miniagent" / "assistant" / "engine" / "cli_tui_keybindings.py"
)


def _tui_source() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in (_TRANSCRIPT_OPS_PY, _KEYBINDINGS_PY, _CLI_TUI_PY)
    )


def _ctrl_l_handler_block(source: str) -> str:
    start = source.index("def on_clear(")
    end = source.index("def on_tasks(", start)
    return source[start:end]


def _reset_and_reload_transcript_block(source: str) -> str:
    start = source.index("def _reset_and_reload_transcript(")
    end = source.index("def _trigger_lazy_load_more_history(", start)
    return source[start:end]


def _load_initial_history_block(source: str) -> str:
    start = source.index("def load_initial_history(")
    end = source.index("def _reset_and_reload_transcript(", start)
    return source[start:end]


def _lazy_load_history_block(source: str) -> str:
    start = source.index("def _trigger_lazy_load_more_history(")
    end = source.index("def recheck_md_width(", start)
    return source[start:end]


def test_pageup_pagedown_use_apply_transcript_scroll() -> None:
    source = _tui_source()
    assert re.search(
        r'self\.apply_transcript_scroll\(-self\.scroll_step\(\), "pageup"\)',
        source,
    )
    assert re.search(
        r'self\.apply_transcript_scroll\(self\.scroll_step\(\), "pagedown"\)',
        source,
    )


def test_ctrl_l_uses_reset_and_reload_transcript() -> None:
    block = _ctrl_l_handler_block(_tui_source())
    assert "self.reset_and_reload_transcript(reset_scroll_to_top=True)" in block


def test_ctrl_l_no_invalid_output_scroll_assignment() -> None:
    source = _tui_source()
    assert not re.search(r"output_scroll\.horizontal_scroll\s*=\s*0", source)


def test_scrollbar_style_high_contrast() -> None:
    source = _tui_source()
    assert re.search(r'"scrollbar\.button":\s*"bg:ansibrightcyan', source)


def test_reset_and_reload_transcript_resets_scroll_when_requested() -> None:
    block = _reset_and_reload_transcript_block(_tui_source())
    assert "if reset_scroll_to_top:" in block
    assert "scroll_pane = self.sp()" in block
    assert "scroll_pane.vertical_scroll = 0" in block
    assert "self.reset_horizontal_scroll()" in block


def test_transcript_uses_explicit_character_limit_instead_of_fixed_deque_maxlen() -> None:
    source = _tui_source()
    assert "_transcript = TranscriptBuffer(_MAX_TRANSCRIPT_CHARS)" in source
    assert "deque(maxlen=5000)" not in source


def test_transcript_prepend_delegates_accounting_and_trim_to_buffer() -> None:
    source = _tui_source()
    start = source.index("def transcript_prepend(")
    end = source.index("def render_history_message(", start)
    block = source[start:end]

    assert "self.transcript.prepend((style, text))" in block
    assert "_transcript_total_len" not in block


def test_initial_history_hint_is_prepended_above_loaded_messages() -> None:
    block = _load_initial_history_block(_tui_source())
    assert "self.transcript_prepend(" in block
    assert "history_load_hint(history_remaining(total, end))" in block


def test_lazy_history_prepend_reverses_loaded_batch_before_rendering() -> None:
    block = _lazy_load_history_block(_tui_source())
    assert "for message in messages_for_prepend(messages):" in block
    assert "history_loaded_end(" in block
