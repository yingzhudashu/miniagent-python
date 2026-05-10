"""MCP（Model Context Protocol）可选集成。

安装 ``pip install miniagent-python[mcp]`` 后可通过 :mod:`miniagent.mcp.bridge` 连接 stdio 服务端
并将工具描述转为 OpenAI Chat Completions 的 ``tools`` 条目。

未安装 ``mcp`` 包时，导入子模块仍可用，但 ``is_mcp_available()`` 为 False。
"""

from miniagent.mcp.bridge import is_mcp_available, mcp_tool_to_openai_param
from miniagent.mcp.runtime import register_mcp_stdio_tools

__all__ = ["is_mcp_available", "mcp_tool_to_openai_param", "register_mcp_stdio_tools"]
