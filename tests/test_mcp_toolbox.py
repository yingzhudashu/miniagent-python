"""MCP 工具箱元数据与 ensure_mcp_toolbox。"""

from __future__ import annotations

from miniagent.infrastructure.registry import DefaultToolRegistry
from miniagent.mcp.toolbox import (
    MCP_TOOLBOX,
    ensure_mcp_toolbox,
    registry_has_mcp_tools,
)
from miniagent.types.tool import ToolDefinition, ToolResult


async def _h(_args: dict, _ctx) -> ToolResult:
    return ToolResult(True, "")


def _schema(name: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": "d",
            "parameters": {"type": "object", "properties": {}},
        },
    }


def test_registry_has_mcp_tools_false_when_empty() -> None:
    reg = DefaultToolRegistry()
    assert registry_has_mcp_tools(reg) is False


def test_registry_has_mcp_tools_true_when_mcp_toolbox() -> None:
    reg = DefaultToolRegistry()
    reg.register(
        "mcp_echo",
        ToolDefinition(
            schema=_schema("mcp_echo"),
            handler=_h,
            permission="allowlist",
            help_text="",
            toolbox="mcp",
        ),
    )
    assert registry_has_mcp_tools(reg) is True


def test_ensure_mcp_toolbox_appends_once() -> None:
    reg = DefaultToolRegistry()
    reg.register(
        "mcp_echo",
        ToolDefinition(
            schema=_schema("mcp_echo"),
            handler=_h,
            permission="allowlist",
            help_text="",
            toolbox="mcp",
        ),
    )
    base: list = []
    out = ensure_mcp_toolbox(base, reg)
    assert len(out) == 1
    assert out[0].id == MCP_TOOLBOX.id
    assert ensure_mcp_toolbox(out, reg) is out


def test_ensure_mcp_toolbox_noop_without_mcp_tools() -> None:
    reg = DefaultToolRegistry()
    base: list = []
    assert ensure_mcp_toolbox(base, reg) == base
