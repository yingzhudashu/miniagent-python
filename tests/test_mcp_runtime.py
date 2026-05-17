"""MCP 桥接与 runtime 辅助（无需真实 stdio 服务端）。"""

from __future__ import annotations

import builtins
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from miniagent.mcp.bridge import is_mcp_available, mcp_tool_to_openai_param
from miniagent.mcp.runtime import _tool_result_text


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


def test_is_mcp_available_is_bool() -> None:
    assert isinstance(is_mcp_available(), bool)


@pytest.mark.asyncio
async def test_register_mcp_stdio_tools_raises_when_mcp_import_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """强制 mcp 导入失败时抛出 RuntimeError（不依赖环境是否已装 mcp extra）。"""
    from miniagent.infrastructure.registry import DefaultToolRegistry
    from miniagent.mcp.runtime import register_mcp_stdio_tools

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

    from miniagent.infrastructure.registry import DefaultToolRegistry
    from miniagent.mcp.runtime import register_mcp_stdio_tools

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
