"""Output-format regressions for ThinkingDisplay."""

from __future__ import annotations

import importlib.util
from collections.abc import Callable
from unittest.mock import patch

import pytest

from miniagent.assistant.engine.thinking import ThinkingDisplay

_HAS_PROMPT_TOOLKIT = importlib.util.find_spec("prompt_toolkit") is not None


def _captured_colors(emit: Callable[[], None]) -> list[str]:
    captured: list[str] = []

    def fake_print(formatted_text, *_args: object, **_kwargs: object) -> None:
        captured.extend(style for style, _ in formatted_text)

    with patch("miniagent.assistant.engine.thinking.print_formatted_text", fake_print):
        emit()
    return captured


@pytest.mark.skipif(not _HAS_PROMPT_TOOLKIT, reason="requires prompt_toolkit")
@pytest.mark.parametrize(
    ("emit", "expected"),
    [
        (lambda display: display._emit("hello"), ["ansigray"]),
        (lambda display: display._emit_line("hello"), ["ansigray"]),
        (lambda display: display._emit("hello", "gray"), ["ansigray"]),
    ],
    ids=["emit-default", "emit-line-default", "emit-explicit"],
)
def test_emit_uses_single_ansi_prefix(emit, expected: list[str]) -> None:
    display = ThinkingDisplay()
    colors = _captured_colors(lambda: emit(display))

    assert colors == expected
    assert all("ansiansi" not in color for color in colors)
