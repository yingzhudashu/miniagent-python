"""MCP 桥接与 runtime 辅助（无需真实 stdio 服务端）。"""

from __future__ import annotations

import builtins
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from miniagent.agent.tools.registry import DefaultToolRegistry
from miniagent.agent.types.tool import ToolResult
from miniagent.assistant.mcp.bridge import is_mcp_available, mcp_tool_to_openai_param
from miniagent.assistant.mcp.runtime import (
    _call_tool_to_result,
    _is_mcp_tool_error,
    _tool_result_text,
    close_mcp_connections,
    register_mcp_stdio_tools,
)


async def _h(args: dict, ctx) -> ToolResult:
    return ToolResult(True, "")


def test_mcp_tool_to_openai_param_shape() -> None:
    tool = SimpleNamespace(
        name="echo",
        description="Echo input",
        inputSchema={"type": "object", "properties": {"q": {"type": "string"}}},
    )
    spec = mcp_tool_to_openai_param(tool)
    assert spec["type"] == "function"
    assert spec["function"]["name"] == "echo"
    assert "Echo" in spec["function"]["description"]
    assert spec["function"]["parameters"]["properties"]["q"]


def test_tool_result_text_joins_blocks() -> None:
    block = SimpleNamespace(text="line1")
    res = SimpleNamespace(content=[block])
    assert _tool_result_text(res) == "line1"


def test_tool_result_text_joins_multiple_blocks() -> None:
    res = SimpleNamespace(
        content=[
            SimpleNamespace(text="a"),
            {"text": "b"},
            SimpleNamespace(image=b"ignored"),
        ]
    )
    assert _tool_result_text(res) == "a\nb"


def test_tool_result_text_json_fallback_when_no_text() -> None:
    res = SimpleNamespace(content=[SimpleNamespace(image=b"x")], model_dump=lambda: {"k": 1})
    out = _tool_result_text(res)
    assert '"k": 1' in out


def test_is_mcp_tool_error_variants() -> None:
    assert _is_mcp_tool_error(SimpleNamespace(isError=True)) is True
    assert _is_mcp_tool_error({"isError": True}) is True
    assert _is_mcp_tool_error(SimpleNamespace(is_error=True)) is True
    assert _is_mcp_tool_error(SimpleNamespace(isError=False)) is False


def test_call_tool_to_result_success_and_error() -> None:
    ok = SimpleNamespace(isError=False, content=[SimpleNamespace(text="ok")])
    err = SimpleNamespace(isError=True, content=[SimpleNamespace(text="bad")])
    assert _call_tool_to_result(ok).success is True
    assert _call_tool_to_result(ok).content == "ok"
    assert _call_tool_to_result(err).success is False
    assert _call_tool_to_result(err).content == "bad"


def test_is_mcp_available_is_bool() -> None:
    assert isinstance(is_mcp_available(), bool)


@pytest.mark.asyncio
async def test_close_mcp_connections_exits_owned_contexts_once() -> None:
    from miniagent.assistant.mcp import runtime

    first = MagicMock()
    first.__aexit__ = AsyncMock(return_value=None)
    second = MagicMock()
    second.__aexit__ = AsyncMock(return_value=None)
    runtime._holder[:] = [first, second]

    await close_mcp_connections()
    await close_mcp_connections()

    first.__aexit__.assert_awaited_once_with(None, None, None)
    second.__aexit__.assert_awaited_once_with(None, None, None)
    assert runtime._holder == []


@pytest.mark.asyncio
async def test_register_mcp_stdio_tools_raises_when_mcp_import_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """强制 mcp 导入失败时抛出 RuntimeError（不依赖环境是否已装 mcp extra）。"""
    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        mod = name if isinstance(name, str) else ""
        if mod == "mcp" or mod.startswith("mcp."):
            raise ImportError("test: mcp unavailable")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    reg = DefaultToolRegistry()
    with pytest.raises(RuntimeError, match="未安装 mcp"):
        await register_mcp_stdio_tools(reg, "echo", [])


@pytest.mark.asyncio
async def test_register_mcp_stdio_tools_registers_prefixed_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """mock stdio/session，无需真实 MCP 子进程。"""
    import sys
    import types

    fake_tool = SimpleNamespace(
        name="echo",
        description="echo tool",
        inputSchema={"type": "object", "properties": {}},
    )

    class _FakeSession:
        async def initialize(self) -> None:
            return None

        async def list_tools(self):
            return SimpleNamespace(tools=[fake_tool])

    class _FakeClientSession:
        def __init__(self, _r: object, _w: object) -> None:
            self._inner = _FakeSession()

        async def __aenter__(self) -> _FakeSession:
            return self._inner

        async def __aexit__(self, *_a: object) -> None:
            return None

    def _fake_stdio_client(_params: object):
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=(MagicMock(), MagicMock()))
        cm.__aexit__ = AsyncMock(return_value=None)
        return cm

    mcp_pkg = types.ModuleType("mcp")
    mcp_pkg.ClientSession = _FakeClientSession
    mcp_pkg.StdioServerParameters = lambda **kw: kw
    mcp_client = types.ModuleType("mcp.client")
    mcp_stdio = types.ModuleType("mcp.client.stdio")
    mcp_stdio.stdio_client = _fake_stdio_client
    monkeypatch.setitem(sys.modules, "mcp", mcp_pkg)
    monkeypatch.setitem(sys.modules, "mcp.client", mcp_client)
    monkeypatch.setitem(sys.modules, "mcp.client.stdio", mcp_stdio)

    reg = DefaultToolRegistry()
    n = await register_mcp_stdio_tools(reg, "echo", [])
    assert n == 1
    assert "mcp_echo" in reg.list()


@pytest.mark.asyncio
async def test_register_mcp_handler_returns_failure_on_is_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """handler 应识别 MCP CallToolResult.isError。"""
    import sys
    import types

    fake_tool = SimpleNamespace(
        name="fail",
        description="fail tool",
        inputSchema={"type": "object", "properties": {}},
    )

    class _FakeSession:
        async def initialize(self) -> None:
            return None

        async def list_tools(self):
            return SimpleNamespace(tools=[fake_tool])

        async def call_tool(self, _name: str, _args: dict) -> SimpleNamespace:
            return SimpleNamespace(
                isError=True,
                content=[SimpleNamespace(text="tool failed")],
            )

    class _FakeClientSession:
        def __init__(self, _r: object, _w: object) -> None:
            self._inner = _FakeSession()

        async def __aenter__(self) -> _FakeSession:
            return self._inner

        async def __aexit__(self, *_a: object) -> None:
            return None

    def _fake_stdio_client(_params: object):
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=(MagicMock(), MagicMock()))
        cm.__aexit__ = AsyncMock(return_value=None)
        return cm

    mcp_pkg = types.ModuleType("mcp")
    mcp_pkg.ClientSession = _FakeClientSession
    mcp_pkg.StdioServerParameters = lambda **kw: kw
    mcp_client = types.ModuleType("mcp.client")
    mcp_stdio = types.ModuleType("mcp.client.stdio")
    mcp_stdio.stdio_client = _fake_stdio_client
    monkeypatch.setitem(sys.modules, "mcp", mcp_pkg)
    monkeypatch.setitem(sys.modules, "mcp.client", mcp_client)
    monkeypatch.setitem(sys.modules, "mcp.client.stdio", mcp_stdio)

    reg = DefaultToolRegistry()
    await register_mcp_stdio_tools(reg, "echo", [])
    tool = reg.get("mcp_fail")
    assert tool is not None
    result = await tool.handler({}, None)
    assert result.success is False
    assert result.content == "tool failed"
