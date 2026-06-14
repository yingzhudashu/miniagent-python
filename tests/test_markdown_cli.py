"""CLI Markdown 渲染辅助。"""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _clear_render_cache() -> None:
    from miniagent.engine import markdown_cli

    markdown_cli._render_cache.clear()
    yield
    markdown_cli._render_cache.clear()


def test_render_markdown_respects_raw_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINIAGENT_CLI_RAW_MARKDOWN", "1")
    from miniagent.engine.markdown_cli import render_markdown_to_ansi

    assert render_markdown_to_ansi("# Title\n\nbody", width=50) is None


def test_cli_raw_markdown_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MINIAGENT_CLI_RAW_MARKDOWN", raising=False)
    from miniagent.engine.markdown_cli import cli_raw_markdown_enabled

    with patch("miniagent.infrastructure.json_config.get_config", return_value=True):
        assert cli_raw_markdown_enabled() is True


def test_strip_ansi() -> None:
    from miniagent.engine.markdown_cli import strip_ansi

    assert strip_ansi("\x1b[32mok\x1b[0m") == "ok"


def test_code_fence_heading_not_promoted() -> None:
    from miniagent.engine.markdown_cli import render_markdown_to_ansi, strip_ansi

    md = "```python\n# fake heading\nprint(1)\n```\n\n# Real Title\n\nbody"
    rendered = render_markdown_to_ansi(md, width=80)
    assert rendered is not None
    plain = strip_ansi(rendered)
    assert "# fake heading" in plain
    assert "Real Title" in plain
    assert "fake heading" not in plain.replace("# fake heading", "")


def test_render_cache_no_collision() -> None:
    from miniagent.engine.markdown_cli import render_markdown_to_ansi

    md_a = "x" * 100 + "UNIQUE_A"
    md_b = "x" * 100 + "UNIQUE_B"
    r_a = render_markdown_to_ansi(md_a, width=50)
    r_b = render_markdown_to_ansi(md_b, width=50)
    assert r_a is not None and r_b is not None
    assert r_a != r_b


def test_compute_fence_mask() -> None:
    from miniagent.engine.markdown_cli import _compute_fence_mask

    lines = ["```python", "# inside", "code", "```", "# outside", "text"]
    mask = _compute_fence_mask(lines)
    assert mask == [False, True, True, False, False, False]


def test_console_file_restored_on_render_error() -> None:
    from miniagent.engine import markdown_cli

    console = markdown_cli._get_cached_console(60)
    original_file = markdown_cli._shared_console_original_file[60]
    with patch.object(console, "print", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError, match="boom"):
            markdown_cli.render_markdown_to_ansi("hello **world**", width=60)
    assert console.file is original_file
