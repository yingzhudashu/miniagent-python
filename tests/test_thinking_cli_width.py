"""ThinkingDisplay Rich 宽度与 main 回复区对齐（set_cli_markdown_width）。"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_set_cli_markdown_width_used_for_thinking_rich(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MINIAGENT_CLI_THINKING_RICH", "1")
    seen: list[int] = []

    def fake_render(markdown: str, *, width: int) -> str:
        seen.append(width)
        return "ok"

    monkeypatch.setattr(
        "miniagent.engine.markdown_cli.render_markdown_to_ansi",
        fake_render,
    )
    from miniagent.engine.thinking import ThinkingDisplay

    td = ThinkingDisplay()
    td.set_cli_markdown_width(lambda: 99)

    def sink(text: str, kind: str = "chunk", *, ansi_markdown: str | None = None) -> None:
        pass

    td.set_output_sink(sink)
    await td.show(
        "| a | b |\n|---|---|\n| 1 | 2 |\n",
        streaming=False,
        header="",
    )
    assert seen == [99]
