"""CLI Markdown 渲染辅助。"""

from __future__ import annotations

import pytest


def test_render_markdown_respects_raw_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINIAGENT_CLI_RAW_MARKDOWN", "1")
    from miniagent.engine.markdown_cli import render_markdown_to_ansi

    assert render_markdown_to_ansi("# Title\n\nbody", width=50) is None


def test_strip_ansi() -> None:
    from miniagent.engine.markdown_cli import strip_ansi

    assert strip_ansi("\x1b[32mok\x1b[0m") == "ok"
