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

from miniagent.memory.activity_log import ActivityLogger
from miniagent.memory.context import DefaultContextManager
from miniagent.memory.embedding_search import (
    EmbeddingIndex,
    EmbeddingSearchProvider,
    embedding_search_enabled,
)
from miniagent.memory.keyword_index import (
    KeywordIndex,
    extract_keywords,
    format_search_results,
)
from miniagent.memory.memory_context_service import (
    DefaultMemoryContext,
    DefaultMemoryHistory,
    DefaultMemorySearch,
    create_default_memory_context,
)
from miniagent.memory.runtime import MemoryRuntime, create_memory_runtime
from miniagent.memory.store import (
    DefaultMemoryStore,
    extract_facts,
    format_memory_for_prompt,
    generate_turn_summary,
)

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
