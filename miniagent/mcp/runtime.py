"""可选：通过 stdio 连接 MCP 服务端并将工具注册到 ``ToolRegistry``（进程内长连接）。

由 ``MINIAGENT_MCP_STDIO`` 触发，在 ``engine.init_subsystems`` 中调用。stdio/session 上下文挂在
模块级 ``_holder`` 以防被 GC 关闭。需已安装 ``mcp``（``pip install miniagent-python[mcp]``）。

工具名前缀 ``mcp_`` 与内置工具并存；同名冲突由注册顺序决定，应避免与内置名重复。
"""

from __future__ import annotations

import json
from typing import Any

# 持有 stdio / session 上下文，防止被 GC 关闭
_holder: list[Any] = []


def _tool_result_text(res: Any) -> str:
    parts: list[str] = []
    for block in getattr(res, "content", None) or []:
        txt = getattr(block, "text", None)
        if txt is not None:
            parts.append(str(txt))
            continue
        if isinstance(block, dict) and "text" in block:
            parts.append(str(block["text"]))
    if parts:
        return "\n".join(parts)
    return json.dumps(res.model_dump() if hasattr(res, "model_dump") else str(res), ensure_ascii=False)


async def register_mcp_stdio_tools(
    registry: Any,
    command: str,
    args: list[str],
    *,
    env: dict[str, str] | None = None,
) -> int:
    """连接 MCP stdio 服务端，注册 ``mcp_<name>`` 工具。需已安装 ``mcp`` 包。

    Returns:
        成功注册的工具数量
    """
    from miniagent.mcp.bridge import mcp_tool_to_openai_param
    from miniagent.types.tool import ToolDefinition, ToolResult

    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except ImportError as e:
        raise RuntimeError("未安装 mcp 包: pip install miniagent-python[mcp]") from e

    params = StdioServerParameters(command=command, args=args, env=env)
    stdio_cm = stdio_client(params)
    read_stream, write_stream = await stdio_cm.__aenter__()
    sess_cm = ClientSession(read_stream, write_stream)
    session = await sess_cm.__aenter__()
    await session.initialize()
    _holder.extend([stdio_cm, sess_cm, session])

    listed = await session.list_tools()
    n = 0
    for mcp_tool in listed.tools:
        spec_o: dict[str, Any] = mcp_tool_to_openai_param(mcp_tool)
        orig = str(spec_o["function"]["name"])
        safe = orig.replace("-", "_")
        reg_name = f"mcp_{safe}" if not safe.startswith("mcp_") else safe
        spec_o["function"]["name"] = reg_name

        def _make_handler(_session: Any, _orig_tool: str):
            async def handler(arguments: dict[str, Any], ctx: Any) -> ToolResult:
                try:
                    res = await _session.call_tool(_orig_tool, arguments)
                    return ToolResult(True, _tool_result_text(res))
                except Exception as ex:
                    return ToolResult(False, f"MCP 调用失败: {ex}")

            return handler

        try:
            registry.register(
                reg_name,
                ToolDefinition(
                    schema=spec_o,  # type: ignore[arg-type]
                    handler=_make_handler(session, orig),
                    permission="allowlist",
                    help_text=f"MCP tool {orig}",
                    toolbox="mcp",
                ),
            )
            n += 1
        except ValueError:
            pass
    return n


__all__ = ["register_mcp_stdio_tools"]
