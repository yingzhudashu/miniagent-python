"""Tests for tool registry."""

import pytest

from miniagent.infrastructure.registry import DefaultToolRegistry
from miniagent.types.tool import ToolDefinition


def make_tool(name: str = "test") -> ToolDefinition:
    """Helper to create a ToolDefinition."""
    return ToolDefinition(
        schema={"type": "function", "function": {"name": name, "description": f"A {name} tool", "parameters": {"type": "object", "properties": {}}}},
        handler=lambda x, ctx: None,  # type: ignore
        permission="sandbox",
        help_text=f"Help for {name}",
    )


class TestDefaultToolRegistry:
    def test_register_and_list(self):
        reg = DefaultToolRegistry()
        reg.register("tool_a", make_tool("tool_a"))
        reg.register("tool_b", make_tool("tool_b"))
        names = reg.list()
        assert "tool_a" in names
        assert "tool_b" in names
        assert len(names) == 2

    def test_register_duplicate_raises(self):
        reg = DefaultToolRegistry()
        reg.register("dup", make_tool("dup"))
        with pytest.raises(ValueError):
            reg.register("dup", make_tool("dup"))

    def test_get_existing_tool(self):
        reg = DefaultToolRegistry()
        reg.register("find", make_tool("find"))
        tool = reg.get("find")
        assert tool is not None
        assert tool.name == "find"

    def test_get_nonexistent_returns_none(self):
        reg = DefaultToolRegistry()
        assert reg.get("ghost") is None

    def test_unregister(self):
        reg = DefaultToolRegistry()
        reg.register("removable", make_tool("removable"))
        assert reg.unregister("removable") is True
        assert reg.get("removable") is None
        # Unregistering again returns False
        assert reg.unregister("removable") is False

    def test_list_empty(self):
        reg = DefaultToolRegistry()
        assert reg.list() == []

    def test_has_tool(self):
        reg = DefaultToolRegistry()
        reg.register("check", make_tool("check"))
        all_tools = reg.get_all()
        assert "check" in all_tools
        assert "ghost" not in all_tools

    def test_count(self):
        reg = DefaultToolRegistry()
        reg.register("one", make_tool("one"))
        reg.register("two", make_tool("two"))
        assert len(reg.list()) == 2
        reg.unregister("one")
        assert len(reg.list()) == 1
