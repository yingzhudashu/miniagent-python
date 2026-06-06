"""CLI transcript 滚动相关源码回归（自 ``scripts/verify_scroll_fix.py`` 迁移）。"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MAIN_PY = _REPO_ROOT / "miniagent" / "engine" / "main.py"


def _main_source() -> str:
    return _MAIN_PY.read_text(encoding="utf-8")


def _ctrl_l_handler_block(source: str) -> str:
    start = source.index('@kb.add("c-l", filter=has_focus(input_buffer))')
    end = source.index('@kb.add("c-t", filter=has_focus(input_buffer))', start)
    return source[start:end]


def test_pageup_pagedown_use_apply_transcript_scroll() -> None:
    source = _main_source()
    assert re.search(
        r'_apply_transcript_scroll\(-_scroll_step\(\), "pageup"\)',
        source,
    )
    assert re.search(
        r'_apply_transcript_scroll\(_scroll_step\(\), "pagedown"\)',
        source,
    )


def test_ctrl_l_uses_reset_horizontal_scroll() -> None:
    block = _ctrl_l_handler_block(_main_source())
    assert "_reset_horizontal_scroll()" in block


def test_ctrl_l_no_invalid_output_scroll_assignment() -> None:
    source = _main_source()
    assert not re.search(r"output_scroll\.horizontal_scroll\s*=\s*0", source)


def test_scrollbar_style_high_contrast() -> None:
    source = _main_source()
    assert re.search(r'"scrollbar\.button":\s*"bg:ansibrightcyan', source)


def test_ctrl_l_uses_sp_helper() -> None:
    block = _ctrl_l_handler_block(_main_source())
    assert re.search(r"sp\s*=\s*_sp\(\)", block)
