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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from miniagent.infrastructure.logger import get_logger
from miniagent.types.memory import MemoryEntry, MemoryEntryInput

_logger = get_logger(__name__)


# ============================================================================
# 配置
# ============================================================================


def _is_truthy(val: str | None) -> bool:
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
    """嵌入索引中的一条记录。"""

    text: str
    embedding: list[float]
    session_id: str
    timestamp: str
    user_snippet: str
    summary: str
    facts: list[str] = field(default_factory=list)
    text_hash: str = ""


@dataclass
class EmbeddingSearchResult:
    """嵌入搜索结果。"""

    session_id: str
    timestamp: str
    user_snippet: str
    summary: str
    facts: list[str] = field(default_factory=list)
    score: float = 0.0


class EmbeddingIndex:
    """基于 JSON 文件的轻量嵌入索引。

    每条记忆缓存其向量表示，避免重复调用 API。
    上限 ``max_entries``（默认 10000），超限时驱逐最早条目。
    """

    def __init__(self, state_dir: str = "workspaces") -> None:
        self._state_dir = state_dir
        self._entries: collections.OrderedDict[str, _EmbeddingEntry] = collections.OrderedDict()
        self._dim: int = int(os.environ.get("MINIAGENT_EMBED_DIM", "1536"))
        self._max_entries: int = int(os.environ.get("MINIAGENT_EMBED_MAX_ENTRIES", "10000"))
        self._loaded = False
        self._dirty = False
        self._index_file = os.path.join(state_dir, "embedding-index.json")

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self._load()

    def _load(self) -> None:
        try:
            if not os.path.exists(self._index_file):
                self._loaded = True
                return

            with open(self._index_file, encoding="utf-8") as f:
                disk = json.load(f)

            self._dim = disk.get("dim", self._dim)
            self._entries.clear()
            for key, data in disk.get("entries", {}).items():
                self._entries[key] = _EmbeddingEntry(
                    text=data.get("text", ""),
                    embedding=data.get("embedding", []),
                    session_id=data["session_id"],
                    timestamp=data["timestamp"],
                    user_snippet=data["user_snippet"],
                    summary=data.get("summary", ""),
                    facts=data.get("facts", []),
                    text_hash=data.get("text_hash", ""),
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
                "version": 1,
                "dim": self._dim,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "total_entries": len(self._entries),
                "entries": {
                    k: {
                        "text": e.text,
                        "embedding": e.embedding,
                        "session_id": e.session_id,
                        "timestamp": e.timestamp,
                        "user_snippet": e.user_snippet,
                        "summary": e.summary,
                        "facts": e.facts,
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
        return f"{session_id}:{timestamp}"

    def _indexable_text(self, entry: MemoryEntryInput | MemoryEntry) -> str:
        facts = getattr(entry, "facts", []) or []
        return " ".join([entry.user_snippet, entry.summary, *facts])

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

        key = self._make_key(session_id, entry.timestamp)
        idx_text = self._indexable_text(entry)
        text_hash = _text_hash(idx_text)

        # 如果已有相同 hash 的缓存，跳过
        if key in self._entries and self._entries[key].text_hash == text_hash:
            return

        if embedding is not None:
            if self._dim == 0:
                self._dim = len(embedding)
            self._entries[key] = _EmbeddingEntry(
                text=idx_text,
                embedding=embedding,
                session_id=session_id,
                timestamp=entry.timestamp,
                user_snippet=entry.user_snippet,
                summary=entry.summary,
                facts=getattr(entry, "facts", []) or [],
                text_hash=text_hash,
            )
            self._entries.move_to_end(key)
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

        Args:
            query_embedding: 查询文本的嵌入向量
            limit: 最多返回条数
            min_score: 最低余弦相似度阈值

        Returns:
            按相关性排序的搜索结果
        """
        self._ensure_loaded()

        if not query_embedding or not self._entries:
            return []

        scored: list[EmbeddingSearchResult] = []
        for entry in self._entries.values():
            if not entry.embedding:
                continue
            sim = _cosine_similarity(query_embedding, entry.embedding)
            if sim >= min_score:
                scored.append(
                    EmbeddingSearchResult(
                        session_id=entry.session_id,
                        timestamp=entry.timestamp,
                        user_snippet=entry.user_snippet,
                        summary=entry.summary,
                        facts=entry.facts,
                        score=sim,
                    )
                )

        scored.sort(key=lambda r: r.score, reverse=True)
        return scored[:limit]

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

    def __init__(self, state_dir: str = "workspaces") -> None:
        self._index = EmbeddingIndex(state_dir=state_dir)
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


# ============================================================================
# 便捷函数
# ============================================================================

_embed_provider: EmbeddingSearchProvider | None = None


def get_embed_provider(state_dir: str = "workspaces") -> EmbeddingSearchProvider:
    """获取或创建全局嵌入搜索提供者。"""
    global _embed_provider
    if _embed_provider is None:
        _embed_provider = EmbeddingSearchProvider(state_dir=state_dir)
    return _embed_provider


def reset_embed_provider() -> None:
    """重置全局嵌入搜索提供者（测试用）。"""
    global _embed_provider
    _embed_provider = None


def embedding_search_enabled_flag() -> bool:
    """环境变量控制开关。"""
    return embedding_search_enabled()


__all__ = [
    "EmbeddingIndex",
    "EmbeddingSearchProvider",
    "EmbeddingSearchResult",
    "embedding_search_enabled",
    "get_embed_provider",
    "reset_embed_provider",
]
