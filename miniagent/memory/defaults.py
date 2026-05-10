"""进程级默认记忆三元组 — 与 ``compat.unified_entry`` 同源构造逻辑。

未通过 ``RuntimeContext`` / ``execute_plan`` 注入时，回落到此处缓存的单套实例，
目录根由 ``MINI_AGENT_STATE``（默认 ``<cwd>/workspaces``）决定。
"""

from __future__ import annotations

import os
from typing import Any

_bundle: tuple[Any, Any, Any] | None = None


def get_state_root() -> str:
    """与 ``unified_entry`` 一致的状态根目录。"""
    return os.environ.get("MINI_AGENT_STATE", os.path.join(os.getcwd(), "workspaces"))


def reset_process_default_memory_bundle_for_tests() -> None:
    """清空缓存，仅供测试在更改 ``MINI_AGENT_STATE`` 等环境后重新构造 bundle。"""
    global _bundle
    _bundle = None


def get_process_default_memory_bundle() -> tuple[Any, Any, Any]:
    """返回 (memory_store, activity_log, keyword_index)，进程内惰性单例。"""
    global _bundle
    if _bundle is None:
        from miniagent.memory.activity_log import ActivityLogger
        from miniagent.memory.keyword_index import KeywordIndex
        from miniagent.memory.store import DefaultMemoryStore

        state_root = get_state_root()
        keyword_index = KeywordIndex(state_dir=state_root)
        memory_store = DefaultMemoryStore(state_dir=state_root, keyword_index=keyword_index)
        activity_log = ActivityLogger(base_dir=os.path.join(state_root, "memory"))
        _bundle = (memory_store, activity_log, keyword_index)
    return _bundle


def resolve_memory_dependencies(
    memory_store: Any | None,
    activity_log: Any | None,
    keyword_index: Any | None,
) -> tuple[Any, Any, Any]:
    """合并显式注入与默认 bundle：缺项从 bundle 补；索引优先使用 store 上已绑定的实例。"""
    bms, bal, bki = get_process_default_memory_bundle()
    ms = memory_store if memory_store is not None else bms
    al = activity_log if activity_log is not None else bal
    if keyword_index is not None:
        ki = keyword_index
    else:
        inner = getattr(ms, "_keyword_index", None)
        ki = inner if inner is not None else bki
    return ms, al, ki


__all__ = [
    "get_state_root",
    "get_process_default_memory_bundle",
    "resolve_memory_dependencies",
    "reset_process_default_memory_bundle_for_tests",
]
