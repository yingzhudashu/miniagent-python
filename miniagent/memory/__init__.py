"""记忆子系统（跨会话持久化、检索与分层摘要）

与 ``UnifiedEngine`` 内按会话维护的 ``conversation_history`` 不同：本包提供 **可写入磁盘的
长期记忆、活动日志、关键词索引** 以及 **归档 / 分层长期记忆 / 周期精炼**（见同包内
``history_archive``、``history_bridge``、``layered_memory``、``memory_pipeline``、
``dream_scheduler``；它们由引擎在适当时机调用，不必经本 ``__all__`` 再导出）。

进程默认三元组由 ``defaults.get_process_default_memory_bundle()`` 构造，根目录受
``MINI_AGENT_STATE`` 影响，并与 ``compat.unified_entry`` 注入 ``RuntimeContext`` 的路径一致。
三层记忆与用户可见语义见 ``docs/MEMORY_SYSTEM.md``。

本 ``__init__`` 聚合最常用的可导入符号：

- ``DefaultContextManager``、``DefaultMemoryStore``、``ActivityLogger``、``KeywordIndex``
- ``get_process_default_memory_bundle``、``get_state_root``、``resolve_memory_dependencies``
"""

from __future__ import annotations

from miniagent.memory.activity_log import ActivityLogger
from miniagent.memory.context import DefaultContextManager
from miniagent.memory.defaults import (
    get_process_default_memory_bundle,
    get_state_root,
    resolve_memory_dependencies,
)
from miniagent.memory.keyword_index import (
    KeywordIndex,
    extract_keywords,
    format_search_results,
    get_index_stats,
    search_relevant_memory,
)
from miniagent.memory.store import (
    DefaultMemoryStore,
    extract_facts,
    format_memory_for_prompt,
    generate_turn_summary,
)

__all__ = [
    "DefaultContextManager",
    "DefaultMemoryStore",
    "extract_facts",
    "generate_turn_summary",
    "format_memory_for_prompt",
    "ActivityLogger",
    "KeywordIndex",
    "extract_keywords",
    "search_relevant_memory",
    "format_search_results",
    "get_index_stats",
    "get_state_root",
    "get_process_default_memory_bundle",
    "resolve_memory_dependencies",
]
