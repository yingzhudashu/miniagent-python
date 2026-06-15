"""可选：通过 stdio 连接 MCP 服务端并将工具注册到 ``ToolRegistry``（进程内长连接）。

由 ``config.user.json`` 的 ``mcp.stdio_command`` 触发，在 ``engine.init_subsystems`` 中调用。
stdio/session 上下文挂在模块级 ``_holder`` 以防被 GC 关闭。
需已安装 ``mcp``（``pip install miniagent-python[mcp]``）。

工具名前缀 ``mcp_`` 与内置工具并存；同名冲突由注册顺序决定，应避免与内置名重复。
注册成功后 ``ensure_mcp_toolbox`` 会将 ``mcp`` 工具箱加入规划器可见列表（见 ``toolbox.py``）。

``permission="allowlist"`` 表示该工具不走路径沙箱（与 ``exec_command`` 的命令白名单无关）。
"""

from __future__ import annotations

import atexit
import json
import logging
from typing import Any

from miniagent.types.tool import ToolResult

_logger = logging.getLogger(__name__)

_MCP_MISSING_MSG = "未安装 mcp 包: pip install miniagent-python[mcp]"

# 持有 stdio / session 上下文，防止被 GC 关闭
_holder: list[Any] = []
_registered_atexit = False


def _is_mcp_tool_error(res: Any) -> bool:
    """MCP ``CallToolResult`` 是否标记为业务错误（``isError`` / ``is_error``）。"""
    if isinstance(res, dict):
        return bool(res.get("isError") or res.get("is_error"))
    return bool(getattr(res, "isError", False) or getattr(res, "is_error", False))


def _tool_result_text(res: Any) -> str:
    """将 MCP ``CallToolResult.content`` 中的文本块拼成单字符串。

    仅提取 ``text`` 类型内容块；图片、资源等非文本块会被忽略。
    若没有任何文本块，则对整体结果做 JSON 序列化兜底。
    """
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
    return json.dumps(
        res.model_dump() if hasattr(res, "model_dump") else str(res), ensure_ascii=False
    )


def _call_tool_to_result(res: Any) -> ToolResult:
    """将 MCP ``call_tool`` 返回值转为 ``ToolResult``（含 ``isError`` 语义）。"""
    text = _tool_result_text(res)
    if _is_mcp_tool_error(res):
        return ToolResult(False, text or "MCP 工具返回错误")
    return ToolResult(True, text)


async def _aexit_quietly(cm: Any) -> None:
    """尽力 ``await`` 异步上下文管理器的 ``__aexit__``，失败时仅打 debug 日志。"""
    if cm is None or not hasattr(cm, "__aexit__"):
        return
    try:
        await cm.__aexit__(None, None, None)
    except Exception as ex:
        _logger.debug("MCP 连接关闭时异常（可忽略）: %s", ex)


async def _release_mcp_connections() -> None:
    """关闭并清空模块级 MCP 连接（重复注册或注册失败时调用）。"""
    while _holder:
        cm = _holder.pop()
        await _aexit_quietly(cm)


def _cleanup_mcp_holder() -> None:
    """进程退出时清理 MCP 连接句柄。

    atexit 回调为同步函数，无法 ``await`` 异步 ``aclose``；
    此处仅释放 Python 侧引用，确保 ``__aexit__`` 有机会被 GC 触发。
    实际连接关闭由 OS 进程退出负责。
    """
    _holder.clear()


async def register_mcp_stdio_tools(
    registry: Any,
    command: str,
    args: list[str],
    *,
    env: dict[str, str] | None = None,
) -> int:
    """连接 MCP stdio 服务端，注册 ``mcp_<name>`` 工具。需已安装 ``mcp`` 包。

    重复调用会先关闭已有连接再建立新连接。

    Args:
        registry: 工具注册表（需实现 ``register`` 方法）
        command: stdio 子进程可执行文件路径或命令名
        args: 传给子进程的参数列表
        env: 可选环境变量覆盖（``None`` 表示继承当前进程环境）

    Returns:
        成功注册的工具数量

    Raises:
        RuntimeError: 未安装官方 ``mcp`` 包，或连接/初始化 MCP 服务端失败
    """
    global _registered_atexit

    if not _registered_atexit:
        _registered_atexit = True
        atexit.register(_cleanup_mcp_holder)

    from miniagent.mcp.bridge import mcp_tool_to_openai_param
    from miniagent.types.tool import ToolDefinition

    try:
        from mcp.client.stdio import stdio_client

        from mcp import ClientSession, StdioServerParameters
    except ImportError as e:
        raise RuntimeError(_MCP_MISSING_MSG) from e

    await _release_mcp_connections()

    params = StdioServerParameters(command=command, args=args, env=env)
    stdio_cm = stdio_client(params)
    sess_cm: Any = None
    try:
        read_stream, write_stream = await stdio_cm.__aenter__()
        sess_cm = ClientSession(read_stream, write_stream)
        session = await sess_cm.__aenter__()
        await session.initialize()
        _holder.extend([stdio_cm, sess_cm, session])
    except Exception as ex:
        await _aexit_quietly(sess_cm)
        await _aexit_quietly(stdio_cm)
        raise RuntimeError(f"MCP stdio 连接失败: {ex}") from ex

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
                    return _call_tool_to_result(res)
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
        except ValueError as e:
            _logger.debug("MCP工具已注册，跳过: %s", e)
    return n


__all__ = [
    "_call_tool_to_result",
    "_is_mcp_tool_error",
    "_tool_result_text",
    "register_mcp_stdio_tools",
]
