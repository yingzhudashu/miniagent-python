"""Mini Agent Python — 知识库工具

提供知识库检索和文件读取工具，供 Agent 调用。

工具：
- search_knowledge: 检索已挂载的知识库内容
- read_knowledge_file: 读取知识库文件完整内容
- kb_list: 列出已挂载知识库

重构说明：
- 使用 ToolBuilder 简化工具定义
- ``knowledge.as_core=true`` 时为核心工具箱（始终可用）；否则归入 ``knowledge`` 工具箱
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from miniagent.core.constants import KNOWLEDGE_MAX_FILE_CHARS
from miniagent.infrastructure.json_config import get_config
from miniagent.infrastructure.logger import get_logger
from miniagent.knowledge.base import resolve_kb_file_path
from miniagent.tools.base import tool
from miniagent.types.error_prefix import ERROR_PREFIX, WARNING_PREFIX
from miniagent.types.tool import ToolContext, ToolDefinition, ToolResult

_logger = get_logger(__name__)

# ════════════════════════════════════════════════════════
# Handlers
# ════════════════════════════════════════════════════════


async def _search_knowledge_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """检索知识库内容。"""
    query = str(args.get("query", "")).strip()
    if not query:
        return ToolResult(success=False, content=f"{WARNING_PREFIX} query 参数不能为空")

    kb_name = args.get("kb_name")
    if kb_name:
        kb_name = str(kb_name).strip()
    top_k = args.get("top_k")
    if top_k:
        top_k = int(top_k)

    try:
        registry = ctx.knowledge_registry
        if registry is None:
            return ToolResult(success=False, content=f"{ERROR_PREFIX} 知识库服务未注入")
        result = registry.search(query, kb_name=kb_name, top_k=top_k)
        if not result:
            return ToolResult(success=False, content=f"{WARNING_PREFIX} 未找到相关内容")
        return ToolResult(success=True, content=result)
    except Exception as e:
        _logger.error("知识库检索失败: %s", e)
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 检索失败: {e}")


async def _read_knowledge_file_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """读取知识库文件完整内容。"""
    kb_name = str(args.get("kb_name", "")).strip()
    file_path = str(args.get("file_path", "")).strip()

    if not kb_name:
        return ToolResult(success=False, content=f"{WARNING_PREFIX} kb_name 参数不能为空")
    if not file_path:
        return ToolResult(success=False, content=f"{WARNING_PREFIX} file_path 参数不能为空")

    try:
        registry = ctx.knowledge_registry
        if registry is None:
            return ToolResult(success=False, content=f"{ERROR_PREFIX} 知识库服务未注入")
        kb = registry.get_kb(kb_name)
        if not kb:
            return ToolResult(success=False, content=f"{WARNING_PREFIX} 知识库 '{kb_name}' 未挂载")

        full_path = resolve_kb_file_path(kb.path, file_path)
        if not full_path:
            return ToolResult(success=False, content=f"{WARNING_PREFIX} 文件不存在: {file_path}")

        try:
            with open(full_path, encoding="utf-8") as f:
                content = f.read()
        except UnicodeDecodeError:
            with open(full_path, encoding="gbk") as f:
                content = f.read()

        max_chars = int(get_config("knowledge.max_file_chars", KNOWLEDGE_MAX_FILE_CHARS))
        if len(content) > max_chars:
            content = content[:max_chars] + "\n...[已截断]"

        header = f"## 文件: {file_path}\n\n"
        return ToolResult(success=True, content=header + content)
    except Exception as e:
        _logger.error("读取知识库文件失败: %s", e)
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 读取失败: {e}")


async def _kb_list_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """列出已挂载的知识库。"""
    try:
        registry = ctx.knowledge_registry
        if registry is None:
            return ToolResult(success=False, content=f"{ERROR_PREFIX} 知识库服务未注入")
        kb_list = registry.list()

        if not kb_list:
            return ToolResult(
                success=True,
                content="当前未挂载任何知识库。\n使用 `/kb mount <路径>` 挂载知识库。",
            )

        lines = ["## 已挂载知识库\n\n"]
        for kb in kb_list:
            lines.append(f"- **{kb['name']}**: {kb['entries']} 条目, {kb['keywords']} 关键词")
            lines.append(f"  路径: `{kb['path']}`\n")

        return ToolResult(success=True, content="\n".join(lines))
    except Exception as e:
        _logger.error("列出知识库失败: %s", e)
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 获取列表失败: {e}")


# ════════════════════════════════════════════════════════
# Tool Definitions (使用 ToolBuilder)
# ════════════════════════════════════════════════════════

KNOWLEDGE_TOOLBOX = "knowledge"
KNOWLEDGE_TOOL_NAMES = frozenset({"search_knowledge", "read_knowledge_file", "kb_list"})


def _knowledge_toolbox_builder(builder):
    """按 ``knowledge.as_core`` 决定核心或普通工具箱。"""
    if bool(get_config("knowledge.as_core", True)):
        return builder.core()
    return builder.toolbox(KNOWLEDGE_TOOLBOX)


knowledge_tools: dict[str, ToolDefinition] = {
    "search_knowledge": _knowledge_toolbox_builder(
        tool("search_knowledge", "检索已挂载的知识库内容。输入关键词或问题，返回相关文档片段。")
        .param("query", "string", "搜索关键词或问题")
        .optional("kb_name", "string", "知识库名称（可选，默认检索所有已挂载知识库）")
        .optional("top_k", "integer", "返回条目数（默认5）")
        .sandbox()
        .handler(_search_knowledge_handler)
    ).build(),
    "read_knowledge_file": _knowledge_toolbox_builder(
        tool("read_knowledge_file", "读取知识库中的完整文件内容。用于查看检索结果中的特定文件。")
        .param("kb_name", "string", "知识库名称")
        .param("file_path", "string", "文件路径（相对于知识库 files/ 目录）")
        .sandbox()
        .handler(_read_knowledge_file_handler)
    ).build(),
    "kb_list": _knowledge_toolbox_builder(
        tool("kb_list", "列出已挂载的知识库及其统计信息。")
        .sandbox()
        .handler(_kb_list_handler)
    ).build(),
}


def apply_knowledge_toolbox_policy(tool: ToolDefinition) -> ToolDefinition:
    """注册时按配置刷新 knowledge 工具的 toolbox（``register_builtin_tools`` 调用）。"""
    if tool.toolbox not in (None, KNOWLEDGE_TOOLBOX):
        return tool
    as_core = bool(get_config("knowledge.as_core", True))
    desired: str | None = None if as_core else KNOWLEDGE_TOOLBOX
    if tool.toolbox == desired:
        return tool
    return replace(tool, toolbox=desired)


__all__ = ["knowledge_tools", "KNOWLEDGE_TOOLBOX", "KNOWLEDGE_TOOL_NAMES", "apply_knowledge_toolbox_policy"]
