"""内置 ALL_TOOLS 注册与内置优先策略。"""

from __future__ import annotations

import pytest

from miniagent.engine.builtin_tools import register_builtin_tools
from miniagent.infrastructure.registry import DefaultToolRegistry
from miniagent.types.tool import ToolDefinition


def test_register_builtin_tools_populates_registry() -> None:
    reg = DefaultToolRegistry()
    n = register_builtin_tools(reg)
    assert n > 0
    names = reg.list()
    assert "read_file" in names
    assert "fetch_url" in names
    assert "web_search" in names
    assert "browser_extract_text" in names


def test_register_builtin_tools_skips_self_opt_when_disabled(monkeypatch) -> None:
    monkeypatch.setenv("MINIAGENT_SELF_OPT_TOOLS", "0")
    reg = DefaultToolRegistry()
    register_builtin_tools(reg)
    names = reg.list()
    assert "self_inspect" not in names
    assert "read_file" in names


def test_register_builtin_tools_then_duplicate_register_raises() -> None:
    """内置先注册后，同名工具不得静默覆盖（须先 unregister）。"""
    reg = DefaultToolRegistry()
    register_builtin_tools(reg)
    dummy = ToolDefinition(
        schema={
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "x",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        handler=lambda a, c: None,  # type: ignore[misc, assignment]
        permission="sandbox",
        help_text="",
    )
    with pytest.raises(ValueError):
        reg.register("read_file", dummy)
