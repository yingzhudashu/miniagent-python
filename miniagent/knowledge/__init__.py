"""Mini Agent Python — 知识库挂载系统

提供快速挂载本地知识库、文档、资料的能力，通过关键词索引检索并注入到 Agent 上下文。

架构：
- KnowledgeBase：单个知识库（目录/文件集合 + 索引）
- KnowledgeRegistry：知识库注册表（挂载/卸载/检索）

检索流程：
1. 用户输入 → 知识库检索 → kb_context
2. kb_context 注入 system prompt
3. LLM 调用 → 带知识上下文生成回复

详见 docs/KNOWLEDGE_BASE.md。
"""

from __future__ import annotations

from miniagent.knowledge.registry import (
    KnowledgeRegistry,
    get_kb_registry,
    mount_knowledge_base,
    search_knowledge,
    unmount_knowledge_base,
)

__all__ = [
    "KnowledgeRegistry",
    "get_kb_registry",
    "mount_knowledge_base",
    "unmount_knowledge_base",
    "search_knowledge",
]