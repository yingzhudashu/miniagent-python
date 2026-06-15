"""Tests for tool registry."""

import pytest

from miniagent.infrastructure.registry import DefaultToolRegistry
from miniagent.memory.context import DefaultContextManager, estimate_token_estimate
from miniagent.types.tool import (
    ContextManagerProtocol,
    ToolDefinition,
    ToolRegistryProtocol,
)


def make_tool(name: str = "test", *, toolbox: str | None = None) -> ToolDefinition:
    """Helper to create a ToolDefinition."""
    return ToolDefinition(
        schema={
            "type": "function",
            "function": {
                "name": name,
                "description": f"A {name} tool",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        handler=lambda x, ctx: None,  # type: ignore
        permission="sandbox",
        help_text=f"Help for {name}",
        toolbox=toolbox,
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

    def test_get_schemas_by_toolboxes_filters_and_keeps_core(self) -> None:
        reg = DefaultToolRegistry()
        reg.register("core", make_tool("core", toolbox=None))
        reg.register("reader", make_tool("reader", toolbox="file_read"))
        reg.register("writer", make_tool("writer", toolbox="file_write"))

        schemas = reg.get_schemas_by_toolboxes(["file_read"])
        names = {s["function"]["name"] for s in schemas}
        assert names == {"core", "reader"}

        by_box = reg.get_by_toolboxes(["file_read"])
        assert set(by_box.keys()) == {"core", "reader"}

    def test_get_schemas_by_toolboxes_empty_ids_returns_all(self) -> None:
        reg = DefaultToolRegistry()
        reg.register("a", make_tool("a"))
        reg.register("b", make_tool("b", toolbox="file_read"))
        assert len(reg.get_schemas_by_toolboxes([])) == 2
        assert len(reg.get_by_toolboxes([])) == 2

    def test_get_all_returns_copy(self) -> None:
        reg = DefaultToolRegistry()
        reg.register("one", make_tool("one"))
        snapshot = reg.get_all()
        snapshot.clear()
        assert reg.get("one") is not None

    def test_default_registry_satisfies_protocol(self) -> None:
        reg = DefaultToolRegistry()
        assert isinstance(reg, ToolRegistryProtocol)

    def test_default_context_manager_satisfies_protocol(self) -> None:
        cm = DefaultContextManager(context_window=4096, compress_threshold=0.8)
        assert isinstance(cm, ContextManagerProtocol)

    def test_estimate_token_estimate_matches_estimate_tokens(self) -> None:
        est = estimate_token_estimate("hello 世界")
        assert est.char_length == len("hello 世界")
        assert est.tokens > 0
