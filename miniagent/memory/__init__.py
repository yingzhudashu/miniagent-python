"""记忆子系统（跨会话持久化、检索与分层摘要）

与 ``UnifiedEngine`` 内按会话维护的 ``conversation_history`` 不同：本包提供 **可写入磁盘的
长期记忆、活动日志、关键词索引** 以及 **归档 / 分层长期记忆 / 周期精炼**（见同包内
``history_archive``、``history_bridge``、``layered_memory``、``memory_pipeline``、
``dream_scheduler``；它们由引擎在适当时机调用，不必经本 ``__all__`` 再导出）。

进程记忆对象图由 ``runtime.create_memory_runtime()`` 在应用组合根构造，根目录受
``paths.state_dir`` 影响，并由 ``ApplicationContainer`` 统一持有和关闭。
三层记忆与用户可见语义见 ``docs/MEMORY_SYSTEM.md``。

本 ``__init__`` 聚合最常用的可导入符号：

- ``DefaultContextManager``、``DefaultMemoryStore``、``ActivityLogger``、``KeywordIndex``
- ``MemoryRuntime``、``create_memory_runtime``
"""

from __future__ import annotations

import importlib
from typing import Any

_LAZY_EXPORTS = {
    "ActivityLogger": "miniagent.memory.activity_log",
    "DefaultContextManager": "miniagent.memory.context",
    "DefaultMemoryContext": "miniagent.memory.memory_context_service",
    "DefaultMemoryHistory": "miniagent.memory.memory_context_service",
    "DefaultMemorySearch": "miniagent.memory.memory_context_service",
    "DefaultMemoryStore": "miniagent.memory.store",
    "EmbeddingIndex": "miniagent.memory.embedding_search",
    "EmbeddingSearchProvider": "miniagent.memory.embedding_search",
    "KeywordIndex": "miniagent.memory.keyword_index",
    "MemoryRuntime": "miniagent.memory.runtime",
    "create_default_memory_context": "miniagent.memory.memory_context_service",
    "create_memory_runtime": "miniagent.memory.runtime",
    "embedding_search_enabled": "miniagent.memory.embedding_search",
    "extract_facts": "miniagent.memory.store",
    "extract_keywords": "miniagent.memory.keyword_index",
    "format_memory_for_prompt": "miniagent.memory.store",
    "format_search_results": "miniagent.memory.keyword_index",
    "generate_turn_summary": "miniagent.memory.store",
}


def __getattr__(name: str) -> Any:
    """Load historical aggregate exports only when explicitly requested."""
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Expose lazy aggregate names to discovery and documentation tools."""
    return sorted(set(globals()) | set(_LAZY_EXPORTS))

__all__ = [
    "DefaultContextManager",
    "DefaultMemoryStore",
    "DefaultMemoryContext",
    "DefaultMemoryHistory",
    "DefaultMemorySearch",
    "create_default_memory_context",
    "extract_facts",
    "generate_turn_summary",
    "format_memory_for_prompt",
    "ActivityLogger",
    "KeywordIndex",
    "extract_keywords",
    "format_search_results",
    "MemoryRuntime",
    "create_memory_runtime",
    "EmbeddingIndex",
    "EmbeddingSearchProvider",
    "embedding_search_enabled",
]
