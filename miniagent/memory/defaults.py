"""进程级默认记忆三元组 — 与 ``engine.main.unified_main`` 同源构造逻辑。

语义见 ``docs/MEMORY_SYSTEM.md``（默认路径与配置 paths.state_dir）。

未通过 ``RuntimeContext`` / ``execute_plan`` 注入时，回落到此处缓存的单套实例，
目录根由配置决定。
"""

from __future__ import annotations

import atexit
import logging
import os
from typing import Any

from miniagent.infrastructure.json_config import get_config

_logger = logging.getLogger(__name__)

_bundle: tuple[Any, Any, Any] | None = None


def get_state_root() -> str:
    """与配置一致的状态根目录。"""
    return get_config("paths.state_dir", os.path.join(os.getcwd(), "workspaces"))


def reset_process_default_memory_bundle_for_tests() -> None:
    """清空缓存，仅供测试在更改 ``MINIAGENT_PATHS_STATE_DIR`` 等环境后重新构造 bundle。"""
    global _bundle
    _bundle = None
    from miniagent.memory.shared_registry import reset_registry
    reset_registry()


def get_process_default_memory_bundle() -> tuple[Any, Any, Any]:
    """返回 (memory_store, activity_log, keyword_index)，进程内惰性单例。"""
    global _bundle
    if _bundle is None:
        from miniagent.memory.activity_log import ActivityLogger
        from miniagent.memory.keyword_index import KeywordIndex
        from miniagent.memory.shared_registry import get_registry
        from miniagent.memory.store import DefaultMemoryStore

        state_root = get_state_root()
        registry = get_registry(state_root)
        keyword_index = KeywordIndex(state_dir=state_root, registry=registry)
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


def _flush_process_keyword_index_at_exit() -> None:
    """进程退出时尽力将进程级关键词索引、注册表和嵌入索引落盘（静默吞异常）。"""
    if _bundle is None:
        return
    try:
        _bundle[2].save()  # KeywordIndex
        # 同时保存共享注册表
        from miniagent.memory.shared_registry import get_registry, reset_registry

        registry = get_registry()
        registry.save()
        reset_registry()

        # 保存嵌入索引（新增）
        try:
            from miniagent.memory.embedding_search import get_embed_provider, reset_embed_provider

            provider = get_embed_provider()
            if provider is not None:
                provider.index.save()
                reset_embed_provider()
        except Exception as e:
            _logger.debug("保存嵌入索引失败: %s", e)
    except Exception as e:
        _logger.debug("保存关键词索引失败: %s", e)


atexit.register(_flush_process_keyword_index_at_exit)

__all__ = [
    "get_state_root",
    "get_process_default_memory_bundle",
    "resolve_memory_dependencies",
    "reset_process_default_memory_bundle_for_tests",
]
