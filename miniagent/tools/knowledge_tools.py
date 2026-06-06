"""Mini Agent Python — 知识库工具

提供知识库检索和文件读取工具，供 Agent 调用。

RAG 增强说明：
- 默认情况下，knowledge 工具箱作为核心工具箱（toolbox=None），始终可用
- Agent 能主动调用 search_knowledge、read_knowledge_file、kb_list
- 可通过配置 knowledge.as_core=false 降级为普通工具箱
"""

from __future__ import annotations

import os
from typing import Any

from miniagent.infrastructure.json_config import get_config
from miniagent.infrastructure.logger import get_logger
from miniagent.knowledge import get_kb_registry, search_knowledge
from miniagent.types.tool import ToolContext, ToolDefinition, ToolResult

_logger = get_logger(__name__)

# ════════════════════════════════════════════════════════
# 配置：是否将 knowledge 工具箱作为核心工具箱（始终可用）
# ════════════════════════════════════════════════════════

def _get_knowledge_toolbox() -> str | None:
    """获取 knowledge 工具箱的 toolbox 标记。

    默认为核心工具箱（toolbox=None），始终可用。
    可通过配置降级为普通工具箱。

    Returns:
        None（核心工具箱）或 "knowledge"（普通工具箱）
    """
    if not get_config("knowledge.as_core", True):
        return "knowledge"
    # 默认为核心工具箱
    return None

# ════════════════════════════════════════════════════════
# search_knowledge
# ════════════════════════════════════════════════════════

_search_knowledge_schema = {
    "type": "function",
    "function": {
        "name": "search_knowledge",
        "description": "检索已挂载的知识库内容。输入关键词或问题，返回相关文档片段。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词或问题",
                },
                "kb_name": {
                    "type": "string",
                    "description": "知识库名称（可选，默认检索所有已挂载知识库）",
                },
                "top_k": {
                    "type": "integer",
                    "description": "返回条目数（默认5）",
                },
            },
            "required": ["query"],
        },
    },
}


async def _search_knowledge_handler(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
    """检索知识库内容。"""
    query = str(args.get("query", "")).strip()
    if not query:
        return ToolResult(success=False, content="⚠️ query 参数不能为空")

    kb_name = args.get("kb_name")
    if kb_name:
        kb_name = str(kb_name).strip()
    top_k = args.get("top_k")
    if top_k:
        top_k = int(top_k)

    try:
        result = search_knowledge(query, kb_name=kb_name, top_k=top_k)
        if not result:
            return ToolResult(success=False, content="⚠️ 未找到相关内容")
        return ToolResult(success=True, content=result)
    except Exception as e:
        _logger.error("知识库检索失败: %s", e)
        return ToolResult(success=False, content=f"❌ 检索失败: {e}")


# ════════════════════════════════════════════════════════
# read_knowledge_file
# ════════════════════════════════════════════════════════

_read_knowledge_file_schema = {
    "type": "function",
    "function": {
        "name": "read_knowledge_file",
        "description": "读取知识库中的完整文件内容。用于查看检索结果中的特定文件。",
        "parameters": {
            "type": "object",
            "properties": {
                "kb_name": {
                    "type": "string",
                    "description": "知识库名称",
                },
                "file_path": {
                    "type": "string",
                    "description": "文件路径（相对于知识库 files/ 目录）",
                },
            },
            "required": ["kb_name", "file_path"],
        },
    },
}


async def _read_knowledge_file_handler(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
    """读取知识库文件完整内容。"""
    kb_name = str(args.get("kb_name", "")).strip()
    file_path = str(args.get("file_path", "")).strip()

    if not kb_name:
        return ToolResult(success=False, content="⚠️ kb_name 参数不能为空")
    if not file_path:
        return ToolResult(success=False, content="⚠️ file_path 参数不能为空")

    try:
        registry = get_kb_registry()
        kb = registry.get_kb(kb_name)
        if not kb:
            return ToolResult(success=False, content=f"⚠️ 知识库 '{kb_name}' 未挂载")

        # 构建完整路径
        full_path = os.path.join(kb.path, "files", file_path)
        if not os.path.isfile(full_path):
            # 尝试直接路径
            full_path = os.path.join(kb.path, file_path)
            if not os.path.isfile(full_path):
                return ToolResult(success=False, content=f"⚠️ 文件不存在: {file_path}")

        # 读取文件
        try:
            with open(full_path, encoding="utf-8") as f:
                content = f.read()
        except UnicodeDecodeError:
            with open(full_path, encoding="gbk") as f:
                content = f.read()

        # 截断大文件
        from miniagent.core.constants import KNOWLEDGE_MAX_FILE_CHARS
        from miniagent.infrastructure.json_config import get_config

        max_chars = int(get_config("knowledge.max_file_chars", KNOWLEDGE_MAX_FILE_CHARS))
        if len(content) > max_chars:
            content = content[:max_chars] + "\n...[已截断]"

        header = f"## 文件: {file_path}\n\n"
        return ToolResult(success=True, content=header + content)
    except Exception as e:
        _logger.error("读取知识库文件失败: %s", e)
        return ToolResult(success=False, content=f"❌ 读取失败: {e}")


# ════════════════════════════════════════════════════════
# kb_list
# ════════════════════════════════════════════════════════

_kb_list_schema = {
    "type": "function",
    "function": {
        "name": "kb_list",
        "description": "列出已挂载的知识库及其统计信息。",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
}


async def _kb_list_handler(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
    """列出已挂载的知识库。"""
    try:
        registry = get_kb_registry()
        kb_list = registry.list()

        if not kb_list:
            return ToolResult(
                success=True,
                content="当前未挂载任何知识库。\n使用 `.kb mount <path>` 挂载知识库。",
            )

        lines = ["## 已挂载知识库\n\n"]
        for kb in kb_list:
            lines.append(f"- **{kb['name']}**: {kb['entries']} 条目, {kb['keywords']} 关键词")
            lines.append(f"  路径: `{kb['path']}`\n")

        return ToolResult(success=True, content="\n".join(lines))
    except Exception as e:
        _logger.error("列出知识库失败: %s", e)
        return ToolResult(success=False, content=f"❌ 获取列表失败: {e}")


# ════════════════════════════════════════════════════════
# 导出工具字典
# ════════════════════════════════════════════════════════

# 获取 toolbox 标记（核心工具箱或普通工具箱）
_knowledge_toolbox = _get_knowledge_toolbox()

knowledge_tools: dict[str, ToolDefinition] = {
    "search_knowledge": ToolDefinition(
        schema=_search_knowledge_schema,
        handler=_search_knowledge_handler,
        permission="sandbox",
        help_text="检索已挂载的知识库内容",
        toolbox=_knowledge_toolbox,  # 默认为核心工具箱（None），始终可用
    ),
    "read_knowledge_file": ToolDefinition(
        schema=_read_knowledge_file_schema,
        handler=_read_knowledge_file_handler,
        permission="sandbox",
        help_text="读取知识库文件完整内容",
        toolbox=_knowledge_toolbox,  # 默认为核心工具箱（None），始终可用
    ),
    "kb_list": ToolDefinition(
        schema=_kb_list_schema,
        handler=_kb_list_handler,
        permission="sandbox",
        help_text="列出已挂载的知识库",
        toolbox=_knowledge_toolbox,  # 默认为核心工具箱（None），始终可用
    ),
}

__all__ = ["knowledge_tools"]