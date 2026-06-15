"""Mini Agent Python — 记忆条目共享注册表

提供跨索引的共享文本存储，避免 keyword_index 和 embedding_search 重复存储
user_snippet、summary、facts 等文本字段。

注册表以 "session_id:timestamp" 为键存储条目，两个索引只存储键引用，
按需从注册表获取完整文本内容。

内存节省估算：
- 原：每条记忆在两索引各存 ~500 字符文本 → 1000 字符/条
- 新：每条记忆仅在注册表存 ~500 字符 → 500 字符/条
- 节省约 50% 文本存储内存
"""

from __future__ import annotations

import collections
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from miniagent.infrastructure.json_config import get_config
from miniagent.infrastructure.logger import get_logger
from miniagent.types.memory import MemoryEntry, MemoryEntryInput

_logger = get_logger(__name__)


@dataclass
class SharedEntry:
    """共享记忆条目（存储完整文本）。"""

    session_id: str
    timestamp: str
    user_snippet: str
    summary: str
    facts: list[str] = field(default_factory=list)


class MemoryEntryRegistry:
    """记忆条目共享注册表。

    以 OrderedDict 存储，支持上限驱逐（LRU）。
    同一 session_id:timestamp 的条目只存储一份。
    """

    def __init__(self, state_dir: str = "workspaces") -> None:
        """创建注册表；``state_dir`` 决定 ``memory-registry.json`` 路径。"""
        self._state_dir = state_dir
        self._entries: collections.OrderedDict[str, SharedEntry] = collections.OrderedDict()
        self._max_entries: int = get_config("memory.registry_max_entries", 3000)
        self._loaded = False
        self._dirty = False
        self._registry_file = os.path.join(state_dir, "memory-registry.json")

    def _ensure_loaded(self) -> None:
        """确保注册表已从磁盘加载（延迟加载）。"""
        if not self._loaded:
            self._load()

    def _load(self) -> None:
        """从磁盘加载注册表。"""
        try:
            if not os.path.exists(self._registry_file):
                self._loaded = True
                return

            with open(self._registry_file, encoding="utf-8") as f:
                disk = json.load(f)

            self._entries.clear()
            for key, data in disk.get("entries", {}).items():
                self._entries[key] = SharedEntry(
                    session_id=data["session_id"],
                    timestamp=data["timestamp"],
                    user_snippet=data.get("user_snippet", ""),
                    summary=data.get("summary", ""),
                    facts=data.get("facts", []),
                )

            self._loaded = True
            self._dirty = False
        except Exception as e:
            _logger.warning("加载注册表失败，重建中: %s", e)
            self._entries.clear()
            self._loaded = True
            self._dirty = False

    def save(self) -> None:
        """保存注册表到磁盘。"""
        if not self._dirty:
            return
        try:
            os.makedirs(self._state_dir, exist_ok=True)
            disk = {
                "version": 1,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "total_entries": len(self._entries),
                "entries": {
                    k: {
                        "session_id": e.session_id,
                        "timestamp": e.timestamp,
                        "user_snippet": e.user_snippet,
                        "summary": e.summary,
                        "facts": e.facts,
                    }
                    for k, e in self._entries.items()
                },
            }
            with open(self._registry_file, "w", encoding="utf-8") as f:
                json.dump(disk, f, ensure_ascii=False)
            self._dirty = False
        except Exception as e:
            _logger.error("保存注册表失败: %s", e)

    def _make_key(self, session_id: str, timestamp: str) -> str:
        """构造唯一键。"""
        return f"{session_id}:{timestamp}"

    def register(
        self,
        session_id: str,
        entry: MemoryEntryInput | MemoryEntry,
    ) -> str:
        """注册一条记忆条目，返回键。

        Args:
            session_id: 会话 ID
            entry: 记忆条目

        Returns:
            注册键 "session_id:timestamp"
        """
        self._ensure_loaded()

        key = self._make_key(session_id, entry.timestamp)
        new_facts = getattr(entry, "facts", []) or []

        # 检查是否已存在，且内容是否变更
        if key in self._entries:
            existing = self._entries[key]
            # 如果内容变更，更新条目
            if (
                existing.user_snippet != entry.user_snippet
                or existing.summary != entry.summary
                or existing.facts != new_facts
            ):
                self._entries[key] = SharedEntry(
                    session_id=session_id,
                    timestamp=entry.timestamp,
                    user_snippet=entry.user_snippet,
                    summary=entry.summary,
                    facts=new_facts,
                )
                self._entries.move_to_end(key)
                self._dirty = True
            return key

        # 新条目
        self._entries[key] = SharedEntry(
            session_id=session_id,
            timestamp=entry.timestamp,
            user_snippet=entry.user_snippet,
            summary=entry.summary,
            facts=new_facts,
        )
        self._entries.move_to_end(key)
        self._dirty = True

        # 超过上限时驱逐最早条目
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)
            self._dirty = True

        return key

    def get(self, key: str) -> SharedEntry | None:
        """获取条目。"""
        self._ensure_loaded()
        return self._entries.get(key)

    def contains(self, key: str) -> bool:
        """检查键是否存在。"""
        self._ensure_loaded()
        return key in self._entries

    def evict(self, key: str) -> bool:
        """驱逐指定键。"""
        self._ensure_loaded()
        if key in self._entries:
            del self._entries[key]
            self._dirty = True
            return True
        return False

    def remove_session_entries(self, session_id: str) -> list[str]:
        """移除指定会话的全部注册条目并持久化。

        Args:
            session_id: 会话 ID

        Returns:
            被移除的 entry_key 列表
        """
        self._ensure_loaded()
        prefix = f"{session_id}:"
        removed: list[str] = []
        for key in list(self._entries.keys()):
            entry = self._entries[key]
            if key.startswith(prefix) or entry.session_id == session_id:
                del self._entries[key]
                removed.append(key)
        if removed:
            self._dirty = True
            self.save()
        return removed

    def get_stats(self) -> dict[str, Any]:
        """获取统计信息。"""
        self._ensure_loaded()
        return {"total_entries": len(self._entries)}

    def clear(self) -> None:
        """清空注册表（测试用）。"""
        self._entries.clear()
        self._loaded = True
        self._dirty = False


# ============================================================================
# 全局注册表实例
# ============================================================================

_registry: MemoryEntryRegistry | None = None


def get_registry(state_dir: str = "workspaces") -> MemoryEntryRegistry:
    """获取或创建全局注册表实例。"""
    global _registry
    if _registry is None:
        _registry = MemoryEntryRegistry(state_dir=state_dir)
    return _registry


def reset_registry() -> None:
    """重置全局注册表（测试用）。"""
    global _registry
    if _registry is not None:
        _registry.clear()
    _registry = None


__all__ = [
    "SharedEntry",
    "MemoryEntryRegistry",
    "get_registry",
    "reset_registry",
]