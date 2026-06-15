"""Tests for miniagent.mcp.bridge — MCP to OpenAI schema conversion."""

from __future__ import annotations

import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

from miniagent.mcp.bridge import is_mcp_available, list_mcp_tools_openai, mcp_tool_to_openai_param


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
    assert result["function"]["parameters"] == {
        "type": "object",
        "properties": {"x": {"type": "string"}},
    }


@pytest.mark.asyncio
async def test_list_mcp_tools_openai_mock_stdio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """无需真实 MCP 子进程，mock stdio_client 与 session。"""
    fake_tool = _FakeMCPTool(name="probe", description="probe tool")

    class _FakeSession:
        def __init__(self, *_a: object, **_kw: object) -> None:
            pass

        async def initialize(self) -> None:
            return None

        async def list_tools(self):
            return types.SimpleNamespace(tools=[fake_tool])

        async def __aenter__(self) -> _FakeSession:
            return self

        async def __aexit__(self, *_a: object) -> None:
            return None

    def _fake_stdio_client(_params: object):
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=(MagicMock(), MagicMock()))
        cm.__aexit__ = AsyncMock(return_value=None)
        return cm

    mcp_pkg = types.ModuleType("mcp")
    mcp_pkg.ClientSession = _FakeSession
    mcp_pkg.StdioServerParameters = lambda **kw: kw
    mcp_client = types.ModuleType("mcp.client")
    mcp_stdio = types.ModuleType("mcp.client.stdio")
    mcp_stdio.stdio_client = _fake_stdio_client
    monkeypatch.setitem(sys.modules, "mcp", mcp_pkg)
    monkeypatch.setitem(sys.modules, "mcp.client", mcp_client)
    monkeypatch.setitem(sys.modules, "mcp.client.stdio", mcp_stdio)
    monkeypatch.setattr("miniagent.mcp.bridge._MCP_INSTALLED", True)

    out = await list_mcp_tools_openai("echo", ["--flag"], env={"K": "V"})
    assert len(out) == 1
    assert out[0]["function"]["name"] == "probe"


@pytest.mark.asyncio
async def test_list_mcp_tools_openai_raises_when_uninstalled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("miniagent.mcp.bridge._MCP_INSTALLED", False)
    with pytest.raises(RuntimeError, match="未安装 mcp"):
        await list_mcp_tools_openai("x", [])
