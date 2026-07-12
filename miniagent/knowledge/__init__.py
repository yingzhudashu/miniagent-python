"""Mini Agent Python — 知识库挂载系统

提供快速挂载本地知识库、文档、资料的能力，通过关键词索引检索并拼入 Agent 上下文。

架构：
- KnowledgeBase：单个知识库（目录/文件集合 + 索引）
- KnowledgeRegistry：知识库注册表（挂载/卸载/检索）

检索流程：
1. 用户输入 → 知识库检索 → kb_context
2. 执行阶段将 kb_context 放入 current turn user context（规划等阶段放入对应动态 user 上下文）
3. LLM 调用 → 带知识上下文生成回复

检索策略（KB.yaml ``retriever``）：
- ``keyword``：关键词倒排索引（默认）
- ``fulltext``：全文子串匹配

RAG 增强（v2.0.3）：
提供 `retrieve_knowledge_context` 公共函数，供各阶段统一使用，避免代码重复。

详见 docs/KNOWLEDGE_BASE.md。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from miniagent.infrastructure.json_config import get_config
from miniagent.infrastructure.logger import get_logger

if TYPE_CHECKING:
    from miniagent.contracts.knowledge import KnowledgeRegistryProtocol
    from miniagent.knowledge.registry import KnowledgeRegistry

_logger = get_logger(__name__)


def retrieve_knowledge_context(
    registry: KnowledgeRegistryProtocol,
    query: str,
    phase: str = "executor",
    default_top_k: int = 3,
    default_max_chars: int = 4000,
) -> str:
    """知识库检索辅助函数（RAG 增强公共函数）。

    统一的知识库检索接口，供规划、澄清、分类、反思、执行各阶段使用。
    配置项命名规则：`knowledge.{phase}_enabled/top_k/max_chars`。

    Args:
        registry: 由应用组合根注入的知识库注册表
        query: 检索关键词或用户输入
        phase: 阶段名称（planner/clarifier/classifier/reflector/executor）
        default_top_k: 默认返回条目数
        default_max_chars: 默认最大字符数

    Returns:
        格式化的知识库上下文字符串（含 Markdown 标题），失败或禁用时返回空字符串

    Example:
        >>> kb_context = retrieve_knowledge_context(registry, user_input, phase="planner")
        >>> if kb_context:
        >>>     user_parts.append(kb_context)
    """
    kb_enabled = get_config(f"knowledge.{phase}_enabled", True)
    if not kb_enabled:
        return ""

    try:
        kb_list = registry.list()
        if not kb_list:
            return ""

        top_k = get_config(f"knowledge.{phase}_top_k", default_top_k)
        max_chars = get_config(f"knowledge.{phase}_max_chars", default_max_chars)
        result = registry.search(query, top_k=top_k, max_chars=max_chars)

        if result:
            _logger.debug("%s阶段知识库检索: %d chars", phase.capitalize(), len(result))
            # 各阶段标题略有不同，统一使用"相关知识库摘要"
            return f"\n\n## 相关知识库摘要\n\n{result}"
    except Exception as e:
        _logger.debug("%s阶段知识库检索失败（非关键）: %s", phase.capitalize(), e)

    return ""


def __getattr__(name: str) -> Any:
    """Load the stateful knowledge registry only for callers that request it.

    Control and execution stages import :func:`retrieve_knowledge_context` on
    their hot path.  Importing the registry there would also initialize the
    YAML/file-ingest/index stack even when no knowledge base is mounted.
    """
    if name == "KnowledgeRegistry":
        from miniagent.knowledge.registry import KnowledgeRegistry

        globals()[name] = KnowledgeRegistry
        return KnowledgeRegistry
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "KnowledgeRegistry",
    "retrieve_knowledge_context",
]
