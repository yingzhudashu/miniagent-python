"""记忆子系统

导出：
- 上下文管理 (context)
- 记忆存储 (store)
- 活动日志 (activity_log)
- 关键词索引 (keyword_index)
- 进程默认 bundle（defaults）
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
