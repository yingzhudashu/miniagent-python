"""Mini Agent Python — 轻量语义记忆检索（关键词索引）

基于关键词的倒排索引，实现跨会话的语义记忆检索。
不需要向量数据库，纯文本匹配，轻量高效。

工作原理：
1. 每次保存记忆时，自动提取关键词（中文 n-gram + 英文分词）
2. 建立 关键词 → [记忆条目] 的倒排索引
3. 用户新输入时，提取关键词，检索相关记忆
4. 按相关性排序，只取 Top-N 条注入上下文

分词策略（简化版，无外部依赖）：
- 中文：按字符 n-gram（2-gram + 3-gram）
- 英文：按空格和标点分词，去停用词
- 混合：同时应用两种策略

存储：workspaces/keyword-index.json
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from miniagent.types.memory import MemoryEntry, MemoryEntryInput
from miniagent.infrastructure.logger import get_logger

_logger = get_logger(__name__)


# ============================================================================
# 停用词
# ============================================================================

_STOP_WORDS = frozenset([
    # 中文
    "的", "了", "是", "在", "我", "有", "和", "就", "不", "人", "都", "一", "一个",
    "上", "也", "很", "到", "说", "要", "去", "你", "会", "着", "没有", "看", "好",
    "自己", "这", "那", "吗", "吧", "呢", "啊", "呀", "哦", "嗯", "哈",
    # 英文
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could", "should",
    "may", "might", "can", "shall", "of", "in", "to", "for", "with", "on", "at",
    "from", "by", "as", "into", "through", "during", "before", "after",
    "and", "but", "or", "nor", "so", "yet", "both", "either", "neither",
    "not", "only", "own", "same", "than", "too", "very", "just", "because",
    "i", "me", "my", "myself", "we", "our", "you", "your", "he", "him", "his",
    "she", "her", "it", "its", "they", "them", "their", "what", "which", "who",
    "whom", "this", "that", "these", "those", "am",
])


# ============================================================================
# 索引数据结构
# ============================================================================

@dataclass
class _IndexReference:
    """索引中的记忆引用"""
    session_id: str
    timestamp: str
    user_snippet: str
    summary: str
    facts: list[str] = field(default_factory=list)
    weight: float = 1.0


@dataclass
class _IndexEntry:
    """关键词索引条目"""
    keyword: str
    references: list[_IndexReference] = field(default_factory=list)


@dataclass
class _SearchResult:
    """检索结果"""
    session_id: str
    timestamp: str
    user_snippet: str
    summary: str
    facts: list[str] = field(default_factory=list)
    score: float = 0.0


# ============================================================================
# 分词
# ============================================================================

def extract_keywords(text: str) -> list[str]:
    """提取关键词（简化版中文分词 + 英文词元化）

    分词策略：
    - 英文：按空格和标点分词，去除停用词，过滤单字符
    - 中文：提取 2-gram 和 3-gram 字符组合
    - 混合：同时应用两种策略

    Args:
        text: 要提取关键词的文本

    Returns:
        去重后的关键词列表

    Example:
        extract_keywords('我喜欢吃苹果 and AI is cool')
        # → ['喜欢', '欢吃', '吃苹', '苹果', 'ai', 'cool', ...]
    """
    keywords: set[str] = set()

    # 英文分词
    english_words = re.sub(
        r"[^a-z0-9\u4e00-\u9fff\s]", " ", text.lower()
    ).split()
    for w in english_words:
        if len(w) > 1 and w not in _STOP_WORDS:
            keywords.add(w)

    # 中文 2-gram + 3-gram
    chinese_chars = re.sub(r"[^\u4e00-\u9fff]", "", text)
    for i in range(len(chinese_chars) - 1):
        if i + 1 < len(chinese_chars):
            bigram = chinese_chars[i:i + 2]
            if bigram not in _STOP_WORDS:
                keywords.add(bigram)
        if i + 2 < len(chinese_chars):
            trigram = chinese_chars[i:i + 3]
            keywords.add(trigram)

    return list(keywords)


# ============================================================================
# 索引管理
# ============================================================================

class KeywordIndex:
    """关键词倒排索引

    管理记忆的关键词提取、索引构建和相关检索。

    Example:
        idx = KeywordIndex(state_dir="./workspaces")
        idx.index_entry("session-1", MemoryEntryInput(...))
        results = idx.search_relevant("我的投资偏好")
    """

    def __init__(self, state_dir: str = "workspaces") -> None:
        """创建关键词索引

        Args:
            state_dir: 状态存储目录
        """
        self._state_dir = state_dir
        self._index: dict[str, _IndexEntry] = {}
        self._loaded = False
        self._index_file = os.path.join(state_dir, "keyword-index.json")

    def _ensure_loaded(self) -> None:
        """确保索引已从磁盘加载"""
        if not self._loaded:
            self._load()

    def _load(self) -> None:
        """从磁盘加载索引"""
        try:
            if not os.path.exists(self._index_file):
                self._loaded = True
                return

            with open(self._index_file, "r", encoding="utf-8") as f:
                disk = json.load(f)

            self._index.clear()
            for keyword, data in disk.get("index", {}).items():
                refs = [
                    _IndexReference(
                        session_id=r["session_id"],
                        timestamp=r["timestamp"],
                        user_snippet=r["user_snippet"],
                        summary=r["summary"],
                        facts=r.get("facts", []),
                        weight=r.get("weight", 1.0),
                    )
                    for r in data.get("references", [])
                ]
                self._index[keyword] = _IndexEntry(keyword=keyword, references=refs)

            self._loaded = True
        except Exception as e:
            _logger.warning("加载索引失败，重建中: %s", e)
            self._index.clear()
            self._loaded = True

    def load(self) -> None:
        """从磁盘加载索引（公开接口）。"""
        self._load()

    def save(self) -> None:
        """保存索引到磁盘

        通常在应用退出时调用。
        """
        try:
            os.makedirs(self._state_dir, exist_ok=True)
            disk = {
                "version": 1,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "total_entries": len(self._index),
                "index": {
                    k: {
                        "references": [
                            {
                                "session_id": r.session_id,
                                "timestamp": r.timestamp,
                                "user_snippet": r.user_snippet,
                                "summary": r.summary,
                                "facts": r.facts,
                                "weight": r.weight,
                            }
                            for r in v.references
                        ]
                    }
                    for k, v in self._index.items()
                },
            }
            with open(self._index_file, "w", encoding="utf-8") as f:
                json.dump(disk, f, indent=2, ensure_ascii=False)
        except Exception as e:
            _logger.error("保存索引失败: %s", e)

    def index_entry(
        self, session_id: str, entry: MemoryEntryInput | MemoryEntry
    ) -> None:
        """索引一条记忆条目

        Args:
            session_id: 会话 ID
            entry: 记忆条目
        """
        self._ensure_loaded()

        # 组合文本用于提取关键词
        facts = getattr(entry, "facts", []) or []
        full_text = " ".join([
            entry.user_snippet,
            entry.summary,
            *facts,
        ])

        keywords = extract_keywords(full_text)

        for keyword in keywords:
            if keyword not in self._index:
                self._index[keyword] = _IndexEntry(keyword=keyword)

            idx_entry = self._index[keyword]

            # 检查是否已存在相同会话 + 时间戳的引用
            exists = any(
                r.session_id == session_id and r.timestamp == entry.timestamp
                for r in idx_entry.references
            )
            if not exists:
                idx_entry.references.append(_IndexReference(
                    session_id=session_id,
                    timestamp=entry.timestamp,
                    user_snippet=entry.user_snippet,
                    summary=entry.summary,
                    facts=getattr(entry, "facts", []) or [],
                    weight=1.0,
                ))

    def search_relevant(
        self, query: str, limit: int = 10, recent_minutes: int = 0
    ) -> list[_SearchResult]:
        """检索相关记忆

        Args:
            query: 用户查询文本
            limit: 最多返回条数
            recent_minutes: 只检索最近 N 分钟的记忆（0 = 不限制）

        Returns:
            按相关性排序的搜索结果
        """
        self._ensure_loaded()

        query_keywords = extract_keywords(query)
        if not query_keywords:
            return []

        # 时间过滤
        cutoff_time = None
        if recent_minutes > 0:
            from datetime import timedelta
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=recent_minutes)
            cutoff_time = cutoff.isoformat()

        # 为每个候选条目计算相关性分数
        scores: dict[str, _SearchResult] = {}

        for keyword in query_keywords:
            idx_entry = self._index.get(keyword)
            if not idx_entry:
                continue

            for ref in idx_entry.references:
                if cutoff_time and ref.timestamp < cutoff_time:
                    continue

                key = f"{ref.session_id}:{ref.timestamp}"
                if key not in scores:
                    scores[key] = _SearchResult(
                        session_id=ref.session_id,
                        timestamp=ref.timestamp,
                        user_snippet=ref.user_snippet,
                        summary=ref.summary,
                        facts=ref.facts,
                        score=0.0,
                    )

                # 分数 = 匹配关键词数 + 3-gram 权重更高
                weight = 1.5 if len(keyword) >= 3 else 1.0
                scores[key].score += weight

        # 排序：先按分数
        results = sorted(scores.values(), key=lambda r: r.score, reverse=True)
        return results[:limit]

    def format_results(self, results: list[_SearchResult]) -> str:
        """格式化检索结果为可注入 system prompt 的文本

        Args:
            results: 搜索结果列表

        Returns:
            格式化的文本
        """
        if not results:
            return ""

        parts = ["## 相关记忆检索"]
        for r in results:
            time_str = r.timestamp[:16].replace("T", " ")
            parts.append(f"- [{time_str}] {r.user_snippet} → {r.summary}")
            if r.facts:
                for f_item in r.facts[:3]:
                    parts.append(f"    事实: {f_item}")

        return "\n".join(parts)

    def get_stats(self) -> dict[str, Any]:
        """获取索引统计

        Returns:
            包含 total_keywords, total_references, top_keywords 的字典
        """
        self._ensure_loaded()

        total_refs = 0
        keyword_counts: list[dict[str, Any]] = []

        for keyword, entry in self._index.items():
            total_refs += len(entry.references)
            keyword_counts.append({"keyword": keyword, "count": len(entry.references)})

        keyword_counts.sort(key=lambda x: x["count"], reverse=True)

        return {
            "total_keywords": len(self._index),
            "total_references": total_refs,
            "top_keywords": keyword_counts[:20],
        }

    def prune_expired(self, days_old: int = 30) -> int:
        """清理过期的索引条目

        Args:
            days_old: 保留天数，超过此天数的条目将被清理

        Returns:
            清理的条目数
        """
        self._ensure_loaded()

        from datetime import timedelta
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=days_old)
        ).isoformat()
        removed_count = 0

        for entry in self._index.values():
            before = len(entry.references)
            entry.references = [r for r in entry.references if r.timestamp >= cutoff]
            removed_count += before - len(entry.references)

        # 清空关键词
        empty_keys = [
            k for k, v in self._index.items() if not v.references
        ]
        for k in empty_keys:
            del self._index[k]

        if removed_count > 0:
            self.save()

        return removed_count


# ============================================================================
# 便捷函数（默认索引与进程 bundle 同源）
# ============================================================================


def search_relevant_with_index(
    index: KeywordIndex,
    query: str,
    top_k: int = 5,
    min_score: int = 0,
) -> list[dict[str, Any]]:
    """在给定索引实例上搜索相关记忆（供注入式 KeywordIndex 使用）。"""
    results = index.search_relevant(query, limit=top_k)
    return [
        {
            "session_id": r.session_id,
            "timestamp": r.timestamp,
            "summary": r.summary,
            "user_snippet": r.user_snippet,
            "facts": r.facts,
            "score": r.score,
        }
        for r in results
        if r.score >= min_score
    ]


def search_relevant_memory(
    query: str, top_k: int = 5, min_score: int = 0
) -> list[dict[str, Any]]:
    """搜索相关记忆（全局便捷函数）。"""
    from miniagent.memory.defaults import get_process_default_memory_bundle

    idx = get_process_default_memory_bundle()[2]
    results = idx.search_relevant(query, limit=top_k)
    # 转换为 dict 列表以兼容下游
    return [
        {
            "session_id": r.session_id,
            "timestamp": r.timestamp,
            "summary": r.summary,
            "user_snippet": r.user_snippet,
            "facts": r.facts,
            "score": r.score,
        }
        for r in results
        if r.score >= min_score
    ]


def format_search_results(results: list[dict[str, Any]]) -> str:
    """将搜索结果格式化为可注入 prompt 的文本。"""
    if not results:
        return ""
    lines = ["相关记忆："]
    for r in results:
        lines.append(f"- [{r.get('session_id', '?')}] {r.get('summary', '')[:100]}")
    return "\n".join(lines)


def get_index_stats() -> dict[str, Any]:
    """获取索引统计信息（全局便捷函数）。"""
    from miniagent.memory.defaults import get_process_default_memory_bundle

    return get_process_default_memory_bundle()[2].get_stats()


__all__ = [
    "KeywordIndex",
    "extract_keywords",
    "search_relevant_with_index",
    "search_relevant_memory",
    "format_search_results",
    "get_index_stats",
]
