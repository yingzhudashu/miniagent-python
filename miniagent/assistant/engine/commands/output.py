"""Shared stdout capture and transcript-aware command output helpers."""

from __future__ import annotations

import io
from collections.abc import Callable
from contextlib import redirect_stdout
from typing import Any

from miniagent.agent.types.error_prefix import ERROR_PREFIX

_ANSI_COLOR_TO_STYLE = {
    "ansicyan": "class:cli-user-title",
    "ansigreen": "class:cli-ok",
    "ansired": "class:cli-err",
    "ansiyellow": "class:cli-warn",
    "ansiblue": "class:cli-default",
    "ansimagenta": "class:cli-default",
    "ansiwhite": "class:cli-default",
    "ansibrightcyan": "class:cli-user-title",
    "ansibrightgreen": "class:cli-ok",
    "ansibrightred": "class:cli-err",
    "ansibrightyellow": "class:cli-warn",
    "": "class:cli-default",
}


def capture_output(callable_: Callable[..., Any], *args: Any, **kwargs: Any) -> str:
    """Call a print-oriented command and return its normalized output."""
    buffer = io.StringIO()
    try:
        with redirect_stdout(buffer):
            result = callable_(*args, **kwargs)
    except Exception as error:
        return f"{ERROR_PREFIX} 命令执行失败: {error}"
    return str(result) if isinstance(result, str) else buffer.getvalue().strip()


def command_writer(
    term_write: Any,
    *,
    capture: bool,
    logger: Any,
) -> Callable[[str, str], None]:
    """Create one writer shared by fullscreen, fallback, and captured commands."""
    def write(text: str, color: str = "") -> None:
        if term_write and callable(term_write):
            try:
                term_write(_ANSI_COLOR_TO_STYLE.get(color, "class:cli-default"), text)
            except Exception as error:
                logger.warning("command output callback failed: %s (text=%s)", error, text[:50])
        if not capture:
            print(text)

    return write


__all__ = ["capture_output", "command_writer"]
