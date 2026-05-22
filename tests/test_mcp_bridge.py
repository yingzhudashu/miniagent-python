"""Tests for miniagent.mcp.bridge — MCP to OpenAI schema conversion."""

from __future__ import annotations

from miniagent.mcp.bridge import is_mcp_available, mcp_tool_to_openai_param


def test_is_mcp_available() -> None:
    """MCP package is typically NOT installed in test env; just verify it returns bool."""
    result = is_mcp_available()
    assert isinstance(result, bool)


class _FakeMCPTool:
    """Minimal mock of an MCP Tool object."""

    def __init__(
        self,
        name: str = "test_tool",
        description: str = "A test tool",
        input_schema: dict | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self.inputSchema = input_schema or {"type": "object", "properties": {}}


def test_mcp_tool_to_openai_param_basic() -> None:
    tool = _FakeMCPTool(name="hello", description="says hello")
    result = mcp_tool_to_openai_param(tool)
    assert result["type"] == "function"
    assert result["function"]["name"] == "hello"
    assert result["function"]["description"] == "says hello"
    assert result["function"]["parameters"] == {"type": "object", "properties": {}}


def test_mcp_tool_to_openai_param_truncates_description() -> None:
    tool = _FakeMCPTool(description="x" * 5000)
    result = mcp_tool_to_openai_param(tool)
    assert len(result["function"]["description"]) <= 4096


def test_mcp_tool_to_openai_param_empty_fields() -> None:
    tool = _FakeMCPTool(name="", description="")
    result = mcp_tool_to_openai_param(tool)
    assert result["function"]["name"] == ""
    assert result["function"]["description"] == ""


def test_mcp_tool_to_openai_param_pydantic_schema() -> None:
    class _PydanticLike:
        def model_dump(self) -> dict:
            return {"type": "object", "properties": {"x": {"type": "string"}}}

    tool = _FakeMCPTool(input_schema=_PydanticLike())
    result = mcp_tool_to_openai_param(tool)
    assert result["function"]["parameters"] == {"type": "object", "properties": {"x": {"type": "string"}}}
