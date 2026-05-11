"""引擎工具结果 Markdown 围栏。"""

from __future__ import annotations

from miniagent.engine.engine import _fence_tool_output


def test_fence_tool_output_escapes_inner_fence() -> None:
    inner = "```\ncode\n```"
    out = _fence_tool_output(inner)
    assert out.startswith("````")
    assert inner in out


def test_fence_simple_uses_triple_backtick() -> None:
    out = _fence_tool_output("hello")
    assert out.startswith("```\n")
