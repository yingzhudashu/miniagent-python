"""MCP 工具箱元数据 — 供规划阶段与工具筛选使用。"""

from __future__ import annotations

from typing import Any

from miniagent.agent.types.tool import Toolbox

MCP_TOOLBOX = Toolbox(
    id="mcp",
    name="MCP 外部工具",
    description=(
        "通过 Model Context Protocol stdio 接入的外部工具"
        "（由 config.user.json 的 mcp.stdio_command 配置）"
    ),
    keywords=["mcp", "外部", "stdio", "插件", "model context protocol"],
)


def registry_has_mcp_tools(registry: Any) -> bool:
    """注册表中是否存在 toolbox 为 ``mcp`` 的工具。"""
    try:
        all_tools = registry.get_all()
    except Exception:
        return False
    return any(getattr(t, "toolbox", None) == "mcp" for t in all_tools.values())


def ensure_mcp_toolbox(toolboxes: list[Toolbox], registry: Any) -> list[Toolbox]:
    """若注册表含 MCP 工具且列表中尚无 ``mcp`` 工具箱，则追加 ``MCP_TOOLBOX``。"""
    if not registry_has_mcp_tools(registry):
        return toolboxes
    seen = {t.id for t in toolboxes}
    if MCP_TOOLBOX.id in seen:
        return toolboxes
    return [*toolboxes, MCP_TOOLBOX]


__all__ = ["MCP_TOOLBOX", "ensure_mcp_toolbox", "registry_has_mcp_tools"]
