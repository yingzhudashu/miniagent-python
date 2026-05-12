"""MCP 工具 schema 与 OpenAI Chat Completions ``tools`` 条目的桥接。

- ``mcp_tool_to_openai_param``：把 MCP SDK 的 Tool 转为 function schema。
- ``list_mcp_tools_openai``：临时 stdio 连接并列出工具（高级用法；常驻连接见 ``runtime``）。

未安装官方 ``mcp`` 包时 ``is_mcp_available()`` 为 False；安装使用 ``pip install miniagent-python[mcp]``。

Schema 形状需满足 OpenAI Chat Completions ``tools`` 约束（见 ``executor`` 筛选逻辑）。
"""

from __future__ import annotations

from typing import Any

try:
    import mcp  # noqa: F401

    _MCP_INSTALLED = True
except ImportError:
    _MCP_INSTALLED = False


def is_mcp_available() -> bool:
    """当前环境是否已安装官方 ``mcp`` 包（未安装则 stdio 工具注册会失败）。"""
    return _MCP_INSTALLED


def mcp_tool_to_openai_param(tool: Any) -> dict[str, Any]:
    """将 MCP SDK 的 Tool 对象转为 ``ChatCompletionToolParam`` 形状（type=function）。"""
    name = getattr(tool, "name", "") or ""
    desc = getattr(tool, "description", None) or ""
    input_schema = getattr(tool, "inputSchema", None) or getattr(tool, "input_schema", None)
    if input_schema is None:
        input_schema = {"type": "object", "properties": {}}
    if hasattr(input_schema, "model_dump"):
        input_schema = input_schema.model_dump()
    return {
        "type": "function",
        "function": {
            "name": str(name),
            "description": str(desc)[:4096],
            "parameters": input_schema,
        },
    }


async def list_mcp_tools_openai(
    command: str,
    args: list[str],
    *,
    env: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """连接 stdio MCP 服务端并列出工具，返回 OpenAI 形参列表（需已安装 mcp）。"""
    if not _MCP_INSTALLED:
        raise RuntimeError("未安装 mcp 包，请执行: pip install miniagent-python[mcp]")

    from mcp.client.stdio import stdio_client

    from mcp import ClientSession, StdioServerParameters

    params = StdioServerParameters(command=command, args=args, env=env)
    out: list[dict[str, Any]] = []
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            for t in listed.tools:
                out.append(mcp_tool_to_openai_param(t))
    return out


__all__ = [
    "is_mcp_available",
    "mcp_tool_to_openai_param",
    "list_mcp_tools_openai",
]
