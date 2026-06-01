"""Mini Agent Python — 嵌入式语义记忆检索

提供基于向量嵌入的语义搜索。
使用 ``MINIAGENT_EMBED_BASE_URL`` / ``MINIAGENT_EMBED_MODEL`` 配置专用 embedding 服务；
未配置时不使用向量搜索，由调用方回退到关键词索引。

存储：轻量 JSON 文件 ``<state_dir>/embedding-index.json``，每条记忆缓存其向量。
检索：余弦相似度排名，无需外部向量数据库。

环境变量：
- ``MINIAGENT_EMBED_SEARCH``: 默认 ``0``；设 ``1``/``true`` 开启嵌入搜索，否则仅用关键词索引
- ``MINIAGENT_EMBED_BASE_URL``: 专用 embedding 服务 URL
- ``MINIAGENT_EMBED_MODEL``: 专用 embedding 模型
- ``MINIAGENT_EMBED_DIM``: 向量维度（自动从首次响应推断，默认 1536）
- ``MINIAGENT_EMBED_TOP_K``: 最多返回条目数（默认 8）
- ``MINIAGENT_EMBED_MIN_SCORE``: 最低余弦相似度阈值（默认 0.3）
"""

from __future__ import annotations

import collections
import hashlib
import json
import math
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from miniagent.infrastructure.logger import get_logger
from miniagent.memory.shared_registry import MemoryEntryRegistry, get_registry
from miniagent.types.memory import MemoryEntry, MemoryEntryInput

_logger = get_logger(__name__)


# ============================================================================
# 配置
# ============================================================================


def _is_truthy(val: str | None) -> bool:
    """判断字符串环境变量是否为真值（1/true/yes/on）。"""
    if val is None:
        return False  # default = disabled
    return val.strip().lower() in ("1", "true", "yes", "on")


def embedding_search_enabled() -> bool:
    """是否启用嵌入搜索。"""
    return _is_truthy(os.environ.get("MINIAGENT_EMBED_SEARCH"))


def _get_embed_config() -> dict[str, str | int]:
    """专用 embedding 配置（MINIAGENT_EMBED_*）。"""
    base_url = os.environ.get("MINIAGENT_EMBED_BASE_URL", "")
    model = os.environ.get("MINIAGENT_EMBED_MODEL", "")
    api_key = os.environ.get("MINIAGENT_EMBED_API_KEY", "")
    top_k = int(os.environ.get("MINIAGENT_EMBED_TOP_K", "8"))
    min_score = float(os.environ.get("MINIAGENT_EMBED_MIN_SCORE", "0.3"))
    return {
        "base_url": base_url,
        "model": model,
        "api_key": api_key,
        "top_k": top_k,
        "min_score": min_score,
    }


# ============================================================================
# 向量工具
# ============================================================================


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """计算两个向量的余弦相似度。"""
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _text_hash(text: str) -> str:
    """生成文本的短 hash，用于检测内容变更。"""
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:12]


# ============================================================================
# 嵌入 API 调用
# ============================================================================


async def _get_embedding(
    text: str,
    *,
    base_url: str,
    model: str,
    api_key: str,
    timeout: float = 15.0,
) -> list[float]:
    """调用 OpenAI 兼容的 embedding 端点。"""
    if not base_url or not model or not api_key:
        raise ValueError("嵌入配置不完整：需要 base_url、model 和 api_key")

    url = base_url.rstrip("/") + "/embeddings"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "input": text,
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        embedding = data["data"][0]["embedding"]
        return embedding


# ============================================================================
# 嵌入索引
# ============================================================================


@dataclass
class _EmbeddingEntry:
    """嵌入索引中的一条记录（仅存储键和向量，文本从共享注册表获取）。

    **性能优化**：
    - 预计算 norm 值，避免每次搜索重复计算
    """

    embedding: list[float]
    entry_key: str  # "session_id:timestamp"
    text_hash: str = ""  # 用于检测内容变更
    norm: float = 0.0  # 性能优化：预计算的向量 norm


def _compute_norm(embedding: list[float]) -> float:
    """计算向量的 L2 norm（预计算用）。"""
    return math.sqrt(sum(x * x for x in embedding)) if embedding else 0.0


def _cosine_similarity_cached(
    query_embedding: list[float],
    query_norm: float,
    entry: _EmbeddingEntry,
) -> float:
    """性能优化：使用预计算的 norm 计算余弦相似度。

    Args:
        query_embedding: 查询向量
        query_norm: 查询向量的预计算 norm
        entry: 存储的嵌入条目（含预计算 norm）

    Returns:
        余弦相似度
    """
    if not query_embedding or not entry.embedding:
        return 0.0
    if query_norm == 0 or entry.norm == 0:
        return 0.0
    dot = sum(x * y for x, y in zip(query_embedding, entry.embedding))
    return dot / (query_norm * entry.norm)


@dataclass
class EmbeddingSearchResult:
    """嵌入搜索结果（文本从共享注册表获取）。"""

    entry_key: str  # "session_id:timestamp"
    score: float = 0.0


class EmbeddingIndex:
    """基于 JSON 文件的轻量嵌入索引。

    每条记忆缓存其向量表示，避免重复调用 API。
    文本内容存储在共享注册表，索引仅存储键引用。
    上限 ``max_entries``（默认 2000），超限时驱逐最早条目。
    """

    def __init__(
        self,
        state_dir: str = "workspaces",
        registry: MemoryEntryRegistry | None = None,
    ) -> None:
        self._state_dir = state_dir
        self._registry = registry or get_registry(state_dir)
        self._entries: collections.OrderedDict[str, _EmbeddingEntry] = collections.OrderedDict()
        self._dim: int = int(os.environ.get("MINIAGENT_EMBED_DIM", "1536"))
        # 降低默认上限以减少内存占用（10000 条目 ≈ 60MB，2000 ≈ 12MB）
        self._max_entries: int = int(os.environ.get("MINIAGENT_EMBED_MAX_ENTRIES", "2000"))
        self._loaded = False
        self._dirty = False
        self._index_file = os.path.join(state_dir, "embedding-index.json")

    def _ensure_loaded(self) -> None:
        """确保索引已从磁盘加载（延迟加载）。"""
        if not self._loaded:
            self._load()

    def _load(self) -> None:
        """从磁盘加载嵌入索引 JSON 文件。"""
        try:
            if not os.path.exists(self._index_file):
                self._loaded = True
                return

            with open(self._index_file, encoding="utf-8") as f:
                disk = json.load(f)

            self._dim = disk.get("dim", self._dim)
            self._entries.clear()
            for key, data in disk.get("entries", {}).items():
                emb = data.get("embedding", [])
                self._entries[key] = _EmbeddingEntry(
                    embedding=emb,
                    entry_key=data.get("entry_key", key),
                    text_hash=data.get("text_hash", ""),
                    norm=_compute_norm(emb),  # 性能优化：预计算 norm
                )

            self._loaded = True
            self._dirty = False
        except Exception as e:
            _logger.warning("加载嵌入索引失败，重建中: %s", e)
            self._entries.clear()
            self._loaded = True
            self._dirty = False

    def save(self) -> None:
        """保存嵌入索引到磁盘。"""
        if not self._dirty:
            return
        try:
            os.makedirs(self._state_dir, exist_ok=True)
            disk = {
                "version": 2,  # 新版本：仅存储键引用
                "dim": self._dim,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "total_entries": len(self._entries),
                "entries": {
                    k: {
                        "embedding": e.embedding,
                        "entry_key": e.entry_key,
                        "text_hash": e.text_hash,
                    }
                    for k, e in self._entries.items()
                },
            }
            with open(self._index_file, "w", encoding="utf-8") as f:
                json.dump(disk, f, ensure_ascii=False)
            self._dirty = False
        except Exception as e:
            _logger.error("保存嵌入索引失败: %s", e)

    def _make_key(self, session_id: str, timestamp: str) -> str:
        """构造索引条目的唯一键（session_id:timestamp）。"""
        return f"{session_id}:{timestamp}"

    def _indexable_text(self, entry: MemoryEntryInput | MemoryEntry) -> str:
        """构造用于嵌入计算的文本（user_snippet + summary + facts）。"""
        facts = getattr(entry, "facts", []) or []
        return " ".join([entry.user_snippet, entry.summary, *facts])

    def _get_text_from_registry(self, entry_key: str) -> str:
        """从注册表获取可索引文本。"""
        shared = self._registry.get(entry_key)
        if shared is None:
            return ""
        facts = shared.facts or []
        return " ".join([shared.user_snippet, shared.summary, *facts])

    def index_entry(
        self,
        session_id: str,
        entry: MemoryEntryInput | MemoryEntry,
        *,
        embedding: list[float] | None = None,
    ) -> None:
        """索引一条记忆及其嵌入向量。

        Args:
            session_id: 会话 ID
            entry: 记忆条目
            embedding: 预计算的嵌入向量（由外部获取）
        """
        self._ensure_loaded()

        # 注册到共享注册表
        entry_key = self._registry.register(session_id, entry)
        idx_text = self._indexable_text(entry)
        text_hash = _text_hash(idx_text)

        # 如果已有相同 hash 的缓存，跳过
        if entry_key in self._entries and self._entries[entry_key].text_hash == text_hash:
            return

        if embedding is not None:
            if self._dim == 0:
                self._dim = len(embedding)
            self._entries[entry_key] = _EmbeddingEntry(
                embedding=embedding,
                entry_key=entry_key,
                text_hash=text_hash,
                norm=_compute_norm(embedding),  # 性能优化：预计算 norm
            )
            self._entries.move_to_end(entry_key)
            self._dirty = True
            # 超过上限时驱逐最早条目
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
                self._dirty = True

    def search_relevant(
        self,
        query_embedding: list[float],
        *,
        limit: int = 8,
        min_score: float = 0.3,
    ) -> list[EmbeddingSearchResult]:
        """基于预计算的查询向量检索相关记忆。

        **性能优化**：
        - 使用预计算的 norm 值
        - 使用 heapq 实现 top-k 搜索，避免全量排序

        Args:
            query_embedding: 查询文本的嵌入向量
            limit: 最多返回条数
            min_score: 最低余弦相似度阈值

        Returns:
            按相关性排序的搜索结果
        """
        import heapq

        self._ensure_loaded()

        if not query_embedding or not self._entries:
            return []

        # 性能优化：预计算查询向量的 norm
        query_norm = _compute_norm(query_embedding)
        if query_norm == 0:
            return []

        # 性能优化：使用 heapq 实现 top-k
        heap: list[tuple[float, str]] = []  # (score, entry_key)

        for entry in self._entries.values():
            if not entry.embedding or entry.norm == 0:
                continue
            sim = _cosine_similarity_cached(query_embedding, query_norm, entry)
            if sim >= min_score:
                heapq.heappush(heap, (sim, entry.entry_key))
                if len(heap) > limit:
                    heapq.heappop(heap)

        # heapq 是最小堆，结果需要反转排序
        result = [
            EmbeddingSearchResult(entry_key=entry_key, score=score)
            for score, entry_key in sorted(heap, reverse=True)
        ]
        return result

    def get_stats(self) -> dict[str, Any]:
        self._ensure_loaded()
        return {
            "total_embeddings": len(self._entries),
            "dim": self._dim,
        }


# ============================================================================
# 嵌入搜索提供者
# ============================================================================


class EmbeddingSearchProvider:
    """使用 ``MINIAGENT_EMBED_BASE_URL`` / ``MINIAGENT_EMBED_MODEL`` 配置专用 embedding 服务。"""

    def __init__(
        self,
        state_dir: str = "workspaces",
        registry: MemoryEntryRegistry | None = None,
    ) -> None:
        self._registry = registry or get_registry(state_dir)
        self._index = EmbeddingIndex(state_dir=state_dir, registry=self._registry)
        self._providers: list[dict[str, str | int]] = []
        self._init_providers()

    def _init_providers(self) -> None:
        """仅使用 MINIAGENT_EMBED_* 专用配置；未配置时无 embedding，
        由调用方回退到关键词索引。"""
        embed = _get_embed_config()
        if embed["base_url"] and embed["model"] and embed["api_key"]:
            self._providers.append(embed)

    @property
    def index(self) -> EmbeddingIndex:
        return self._index

    async def get_embedding(self, text: str) -> list[float] | None:
        """获取文本的嵌入向量。"""
        clean = re.sub(r"\s+", " ", text).strip()
        if not clean:
            return None

        for provider in self._providers:
            try:
                embedding = await _get_embedding(
                    clean,
                    base_url=str(provider["base_url"]),
                    model=str(provider["model"]),
                    api_key=str(provider["api_key"]),
                )
                return embedding
            except Exception as e:
                _logger.warning("嵌入供应商失败: %s", e)
                continue

        return None

    async def search(
        self,
        query: str,
        *,
        limit: int = 8,
        min_score: float = 0.3,
    ) -> list[EmbeddingSearchResult]:
        """搜索相关记忆。先获取查询向量，再用余弦相似度检索。"""
        query_embedding = await self.get_embedding(query)
        if query_embedding is None:
            return []
        return self._index.search_relevant(
            query_embedding,
            limit=limit,
            min_score=min_score,
        )

    def expand_result(self, result: EmbeddingSearchResult) -> dict[str, Any] | None:
        """从注册表获取完整文本内容。

        Args:
            result: 搜索结果

        Returns:
            包含完整文本的字典，或 None（若条目已不存在）
        """
        shared = self._registry.get(result.entry_key)
        if shared is None:
            return None
        return {
            "session_id": shared.session_id,
            "timestamp": shared.timestamp,
            "user_snippet": shared.user_snippet,
            "summary": shared.summary,
            "facts": shared.facts,
            "score": result.score,
        }

    def expand_results(self, results: list[EmbeddingSearchResult]) -> list[dict[str, Any]]:
        """批量扩展搜索结果。

        Args:
            results: 搜索结果列表

        Returns:
            包含完整文本的字典列表（过滤掉已不存在的条目）
        """
        expanded = []
        for r in results:
            item = self.expand_result(r)
            if item is not None:
                expanded.append(item)
        return expanded


# ============================================================================
# 便捷函数
# ============================================================================

_embed_provider: EmbeddingSearchProvider | None = None


def get_embed_provider(state_dir: str = "workspaces") -> EmbeddingSearchProvider:
    """获取或创建全局嵌入搜索提供者。"""
    global _embed_provider
    if _embed_provider is None:
        registry = get_registry(state_dir)
        _embed_provider = EmbeddingSearchProvider(state_dir=state_dir, registry=registry)
    return _embed_provider


def reset_embed_provider() -> None:
    """重置全局嵌入搜索提供者（测试用）。"""
    global _embed_provider
    _embed_provider = None


__all__ = [
    "EmbeddingIndex",
    "EmbeddingSearchProvider",
    "EmbeddingSearchResult",
    "embedding_search_enabled",
    "get_embed_provider",
    "reset_embed_provider",
    "get_registry",
]
