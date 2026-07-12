"""MCP（Model Context Protocol）可选集成。

安装 ``pip install miniagent-python[mcp]`` 后可通过 :mod:`miniagent.mcp.bridge` 连接 stdio 服务端
并将工具描述转为 OpenAI Chat Completions 的 ``tools`` 条目。

未安装 ``mcp`` 包时，导入子模块仍可用，但 ``is_mcp_available()`` 为 False。

``config.user.json`` 的 ``mcp.stdio_command`` / ``mcp.stdio_env`` 格式见 ``config.defaults.json``；
CI 说明见 ``docs/ENGINEERING.md`` §2。

工具可见性：MCP 工具注册在 ``toolbox="mcp"`` 下。默认 ``tool_selection_strategy="toolbox"`` 时，
规划器需在 ``required_toolboxes`` 中包含 ``mcp`` 才能暴露给 LLM（注册成功后会自动加入工具箱列表）。
``tool_selection_strategy="all"`` 或 ``required_toolboxes=[]`` 时始终可见。
"""

from miniagent.mcp.bridge import is_mcp_available, list_mcp_tools_openai, mcp_tool_to_openai_param
from miniagent.mcp.runtime import close_mcp_connections, register_mcp_stdio_tools
from miniagent.mcp.toolbox import MCP_TOOLBOX, ensure_mcp_toolbox, registry_has_mcp_tools

__all__ = [
    "MCP_TOOLBOX",
    "close_mcp_connections",
    "ensure_mcp_toolbox",
    "is_mcp_available",
    "list_mcp_tools_openai",
    "mcp_tool_to_openai_param",
    "register_mcp_stdio_tools",
    "registry_has_mcp_tools",
]
