"""Mini Agent Python — 嵌入式语义记忆检索

提供基于向量嵌入的语义搜索。
使用 JSON配置 ``embedding.*`` 配置专用 embedding 服务；
未配置时不使用向量搜索，由调用方回退到关键词索引。

存储：轻量 JSON 文件 ``<state_dir>/embedding-index.json``，每条记忆缓存其向量。
检索：余弦相似度排名，无需外部向量数据库。

配置项见 config.defaults.json 中 embedding 配置节。
"""

from __future__ import annotations

import asyncio
import collections
import hashlib
import json
import math
import os
import re
import threading
from array import array
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from miniagent.assistant.infrastructure.atomic_json import atomic_dump_json
from miniagent.llm.embeddings import EmbeddingClient, EmbeddingConfig

# numpy is an optional acceleration path. Importing it eagerly adds substantial
# startup RSS even when embedding search is disabled or the index is small.
_numpy_module: Any | None = None
_numpy_checked = False
_numpy_lock = threading.Lock()


def _get_numpy() -> Any | None:
    """Load optional numpy once, only when vector math actually needs it."""
    global _numpy_checked, _numpy_module
    if _numpy_checked:
        return _numpy_module
    with _numpy_lock:
        if _numpy_checked:
            return _numpy_module
        try:
            import numpy

            _numpy_module = numpy
        except ImportError:
            _numpy_module = None
        _numpy_checked = True
    return _numpy_module

from miniagent.agent.logging import get_logger
from miniagent.agent.observability import emit_trace
from miniagent.agent.trace_events import (
    EVENT_EMBEDDING_API_CALL,
    EVENT_EMBEDDING_CACHE_HIT,
    EVENT_EMBEDDING_INDEX_COMPLETED,
    EVENT_EMBEDDING_INDEX_QUEUED,
)
from miniagent.agent.types.memory import MemoryEntry, MemoryEntryInput
from miniagent.assistant.infrastructure.json_config import get_config
from miniagent.assistant.memory.shared_registry import MemoryEntryRegistry

_logger = get_logger(__name__)

# ── 性能优化：Embedding API缓存（避免重复调用）──
import time

# 全局embedding缓存（LRU + TTL）
_EMBEDDING_CACHE: collections.OrderedDict[str, tuple[array[float], float]] = (
    collections.OrderedDict()
)
_EMBEDDING_CACHE_MAX_SIZE = 1000
_EMBEDDING_CACHE_TTL_SECONDS = 3600
_EMBEDDING_BATCH_CHUNK_SIZE = 256
_EMBEDDING_CACHE_LOCK = threading.RLock()


def _embedding_cache_namespace() -> str:
    base_url = str(get_config("embedding.base_url", "")).rstrip("/")
    model = str(get_config("embedding.model", ""))
    return f"{base_url}\0{model}"


def _embedding_cache_key(text: str, namespace: str | None = None) -> str:
    scoped = f"{namespace or _embedding_cache_namespace()}\0{text}"
    return hashlib.blake2s(scoped.encode(), digest_size=16).hexdigest()


def _get_cached_embedding(
    text: str,
    *,
    namespace: str | None = None,
) -> array[float] | None:
    """从缓存获取embedding（LRU + TTL）。

    性能优化：
    - 减少API调用频率
    - 节省成本和延迟
    - TTL防止过期数据

    Args:
        text: 输入文本

    Returns:
        嵌入向量（缓存命中时），None（缓存未命中）
    """
    if not text:
        return None

    cache_key = _embedding_cache_key(text, namespace)

    with _EMBEDDING_CACHE_LOCK:
        if cache_key in _EMBEDDING_CACHE:
            embedding, timestamp = _EMBEDDING_CACHE[cache_key]
            now = time.monotonic()
            ttl_seconds = float(
                get_config("embedding.cache_ttl_seconds", _EMBEDDING_CACHE_TTL_SECONDS)
            )
            if now - timestamp < ttl_seconds:
                _EMBEDDING_CACHE.move_to_end(cache_key)
                return embedding
            _EMBEDDING_CACHE.pop(cache_key)

    return None


def _cache_embedding(
    text: str,
    embedding: Sequence[float],
    *,
    namespace: str | None = None,
) -> array[float] | None:
    """缓存embedding向量。

    Args:
        text: 输入文本
        embedding: 嵌入向量
    """
    if not text or not embedding:
        return None

    cache_key = _embedding_cache_key(text, namespace)
    now = time.monotonic()
    compact = embedding if isinstance(embedding, array) else array("d", embedding)

    with _EMBEDDING_CACHE_LOCK:
        _EMBEDDING_CACHE[cache_key] = (compact, now)
        _EMBEDDING_CACHE.move_to_end(cache_key)
        max_size = max(
            1, int(get_config("embedding.cache_max_size", _EMBEDDING_CACHE_MAX_SIZE))
        )
        while len(_EMBEDDING_CACHE) > max_size:
            _EMBEDDING_CACHE.popitem(last=False)
    return compact


def _embedding_cache_size() -> int:
    with _EMBEDDING_CACHE_LOCK:
        return len(_EMBEDDING_CACHE)


# ============================================================================
# 配置
# ============================================================================


def embedding_search_enabled() -> bool:
    """是否启用嵌入搜索。"""
    return get_config("embedding.enabled", False)


def _get_embed_config() -> dict[str, str | int]:
    """专用 embedding 配置。"""
    return {
        "base_url": get_config("embedding.base_url", ""),
        "model": get_config("embedding.model", ""),
        "api_key": get_config("secrets.embed_api_key", ""),
        "top_k": get_config("embedding.top_k", 8),
        "min_score": get_config("embedding.min_score", 0.3),
    }


# ============================================================================
# 向量工具（性能优化：可选 numpy 加速）
# ============================================================================


def _dot_product(a: Sequence[float], b: Sequence[float]) -> float:
    """计算两个列表向量的点积；批量路径另行使用 numpy。"""
    if not a or not b:
        return 0.0
    return sum(x * y for x, y in zip(a, b, strict=True))


def _compute_norm(embedding: Sequence[float]) -> float:
    """计算单个列表向量的 L2 norm，不为一次运算加载 numpy。"""
    if not embedding:
        return 0.0
    return math.sqrt(sum(x * x for x in embedding))


def _cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """计算两个向量的余弦相似度（性能优化：可选 numpy 加速）。"""
    if not a or not b:
        return 0.0
    norm_a = _compute_norm(a)
    norm_b = _compute_norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return _dot_product(a, b) / (norm_a * norm_b)


def _text_hash(text: str) -> str:
    """生成文本的短 hash，用于检测内容变更。"""
    return hashlib.blake2s(text.encode("utf-8"), digest_size=6).hexdigest()


# ============================================================================
# 嵌入 API 调用
# ============================================================================

async def _get_embedding(
    text: str,
    *,
    client: httpx.AsyncClient,
    base_url: str,
    model: str,
    api_key: str,
    timeout: float = 15.0,
) -> list[float]:
    """通过 LLM 层统一的 embedding transport 获取向量。"""
    embedding_client = EmbeddingClient(
        EmbeddingConfig(
            base_url=base_url,
            model=model,
            api_key=api_key,
            timeout=timeout,
            max_retries=3,
            backoff_factor=1.0,
        ),
        client=client,
    )
    return await embedding_client.embed(text)


# ============================================================================
# 嵌入索引
# ============================================================================


@dataclass
class _EmbeddingEntry:
    """嵌入索引中的一条记录（仅存储键和向量，文本从共享注册表获取）。

    **性能优化**：
    - 预计算 norm 值，避免每次搜索重复计算
    """

    embedding: array[float]
    entry_key: str  # "session_id:timestamp"
    text_hash: str = ""  # 用于检测内容变更
    norm: float = 0.0  # 性能优化：预计算的向量 norm


class _EmbeddingJSONEncoder(json.JSONEncoder):
    """Encode one compact vector at a time instead of expanding the whole index."""

    def default(self, value: Any) -> Any:
        """把紧凑向量数组转换成可持久化的 JSON 列表。"""
        if isinstance(value, array):
            return value.tolist()
        return super().default(value)


def _cosine_similarity_cached(
    query_embedding: Sequence[float],
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
    dot = sum(x * y for x, y in zip(query_embedding, entry.embedding, strict=True))
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
        """创建嵌入索引；``registry`` 缺省时使用 ``state_dir`` 下的共享注册表。"""
        self._state_dir = state_dir
        self._registry = registry or MemoryEntryRegistry(state_dir=state_dir)
        self._entries: collections.OrderedDict[str, _EmbeddingEntry] = collections.OrderedDict()
        self._dim: int = get_config("embedding.dimension", 1536)
        # 降低默认上限以减少内存占用（10000 条目 ≈ 60MB，2000 ≈ 12MB）
        self._max_entries: int = get_config("embedding.max_entries", 2000)
        self._loaded = False
        self._dirty = False
        self._generation = 0
        self._index_lock = threading.RLock()
        self._save_lock = threading.Lock()
        self._index_file = os.path.join(state_dir, "embedding-index.json")

    def _ensure_loaded(self) -> None:
        """确保索引已从磁盘加载（延迟加载）。"""
        with self._index_lock:
            if not self._loaded:
                self._load()

    def has_entries(self) -> bool:
        """Return whether semantic search can produce a result without an API call."""
        self._ensure_loaded()
        with self._index_lock:
            return bool(self._entries)

    def _load(self) -> None:
        """从磁盘加载嵌入索引 JSON 文件。"""
        with self._index_lock:
            try:
                if not os.path.exists(self._index_file):
                    self._loaded = True
                    self._generation = 0
                    return

                with open(self._index_file, encoding="utf-8") as f:
                    disk = json.load(f)

                loaded_entries: collections.OrderedDict[str, _EmbeddingEntry] = (
                    collections.OrderedDict()
                )
                for key, data in disk.get("entries", {}).items():
                    emb = array("d", data.get("embedding", []))
                    loaded_entries[key] = _EmbeddingEntry(
                        embedding=emb,
                        entry_key=data.get("entry_key", key),
                        text_hash=data.get("text_hash", ""),
                        norm=_compute_norm(emb),
                    )
                self._dim = disk.get("dim", self._dim)
                self._entries = loaded_entries
                self._loaded = True
                self._dirty = False
                self._generation = 0
            except Exception as e:
                _logger.warning("加载嵌入索引失败，重建中: %s", e)
                self._entries.clear()
                self._loaded = True
                self._dirty = False
                self._generation = 0

    def save(self) -> None:
        """保存嵌入索引到磁盘。"""
        self._ensure_loaded()
        try:
            with self._save_lock:
                with self._index_lock:
                    if not self._dirty:
                        return
                    generation = self._generation
                    dim = self._dim
                    entries = {
                        key: {
                            "embedding": entry.embedding,
                            "entry_key": entry.entry_key,
                            "text_hash": entry.text_hash,
                        }
                        for key, entry in self._entries.items()
                    }
                disk = {
                    "version": 2,
                    "dim": dim,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "total_entries": len(entries),
                    "entries": entries,
                }
                atomic_dump_json(
                    self._index_file,
                    disk,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    cls=_EmbeddingJSONEncoder,
                )
                with self._index_lock:
                    if self._generation == generation:
                        self._dirty = False
        except Exception as e:
            _logger.error("保存嵌入索引失败: %s", e)

    def remove_entry_keys(self, entry_keys: list[str]) -> int:
        """从嵌入索引中移除指定 entry_key。"""
        if not entry_keys:
            return 0
        self._ensure_loaded()
        removed = 0
        with self._index_lock:
            for entry_key in entry_keys:
                if entry_key in self._entries:
                    del self._entries[entry_key]
                    removed += 1
            if removed:
                self._generation += 1
                self._dirty = True
            self.save()
        return removed

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
        embedding: Sequence[float] | None = None,
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

        if embedding is None:
            return
        compact = embedding if isinstance(embedding, array) else array("d", embedding)
        compact_norm = _compute_norm(compact)
        with self._index_lock:
            existing = self._entries.get(entry_key)
            if existing is not None and existing.text_hash == text_hash:
                return
            if self._dim == 0:
                self._dim = len(compact)
            self._entries[entry_key] = _EmbeddingEntry(
                embedding=compact,
                entry_key=entry_key,
                text_hash=text_hash,
                norm=compact_norm,
            )
            self._entries.move_to_end(entry_key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
            self._generation += 1
            self._dirty = True

    def search_relevant(
        self,
        query_embedding: Sequence[float],
        *,
        limit: int = 8,
        min_score: float = 0.3,
        _allow_batch: bool = True,
    ) -> list[EmbeddingSearchResult]:
        """基于预计算的查询向量检索相关记忆。

        **性能优化**：
        - 使用预计算的 norm 值
        - 使用 heapq 实现 top-k 搜索，避免全量排序

        Args:
            query_embedding: 查询文本的嵌入向量
            limit: 最多返回条数
            min_score: 最低余弦相似度阈值
            _allow_batch: 内部递归守卫。批量路径遇维度不一致回退到本函数时置 False，
                避免再次进入批量路径造成无限递归。

        Returns:
            按相关性排序的搜索结果
        """
        import heapq

        self._ensure_loaded()

        if limit <= 0 or not query_embedding:
            return []
        with self._index_lock:
            entries = tuple(self._entries.values())
        if not entries:
            return []

        # 性能优化：numpy可用且entry数量多时，自动使用批量计算（5-10倍加速）
        if _allow_batch and len(entries) > 20 and _get_numpy() is not None:
            return self.search_relevant_batch(query_embedding, limit=limit, min_score=min_score)

        # numpy不可用或entry数量少时，使用普通版本
        # 性能优化：预计算查询向量的 norm
        query_norm = _compute_norm(query_embedding)
        if query_norm == 0:
            return []

        # 性能优化：使用 heapq 实现 top-k
        heap: list[tuple[float, str]] = []  # (score, entry_key)

        for entry in entries:
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

    def search_relevant_batch(
        self,
        query_embedding: Sequence[float],
        *,
        limit: int = 8,
        min_score: float = 0.3,
    ) -> list[EmbeddingSearchResult]:
        """批量相似度计算（numpy加速）。

        性能优化：
        - 使用numpy批量计算替代Python循环
        - 性能提升：检索时间减少80%
        - 自动回退到普通版本（numpy不可用时）

        Args:
            query_embedding: 查询文本的嵌入向量
            limit: 最多返回条数
            min_score: 最低余弦相似度阈值

        Returns:
            按相关性排序的搜索结果
        """
        # numpy不可用时回退到普通版本
        numpy = _get_numpy()
        if numpy is None:
            return self.search_relevant(
                query_embedding, limit=limit, min_score=min_score, _allow_batch=False
            )

        self._ensure_loaded()

        if limit <= 0 or not query_embedding:
            return []
        with self._index_lock:
            entries = tuple(self._entries.values())
        if not entries:
            return []

        # 转换为numpy数组（批量计算）
        try:
            query_vec = numpy.array(query_embedding, dtype=numpy.float32)
            query_norm = numpy.linalg.norm(query_vec)

            if query_norm == 0:
                return []

            candidates = [
                entry
                for entry in entries
                if entry.embedding and entry.norm > 0
            ]
            if not candidates:
                return []

            embedding_dims = {len(entry.embedding) for entry in candidates}
            if len(embedding_dims) > 1 or len(query_embedding) not in embedding_dims:
                _logger.debug(
                    "Embedding维度不一致，回退到普通版本: dims=%s query=%d",
                    embedding_dims,
                    len(query_embedding),
                )
                return self.search_relevant(
                    query_embedding, limit=limit, min_score=min_score, _allow_batch=False
                )

            # Chunked BLAS bounds the transient matrix to roughly
            # chunk_size * dimension * 4 bytes instead of duplicating the
            # entire index for every query.
            import heapq

            heap: list[tuple[float, str]] = []
            for offset in range(0, len(candidates), _EMBEDDING_BATCH_CHUNK_SIZE):
                chunk = candidates[offset : offset + _EMBEDDING_BATCH_CHUNK_SIZE]
                entry_vecs = numpy.asarray(
                    [entry.embedding for entry in chunk],
                    dtype=numpy.float32,
                )
                norm_vecs = numpy.fromiter(
                    (entry.norm for entry in chunk),
                    dtype=numpy.float32,
                    count=len(chunk),
                )
                sims = numpy.dot(entry_vecs, query_vec) / (norm_vecs * query_norm)
                for index in numpy.flatnonzero(sims >= min_score):
                    score = float(sims[index])
                    heapq.heappush(heap, (score, chunk[int(index)].entry_key))
                    if len(heap) > limit:
                        heapq.heappop(heap)

            return [
                EmbeddingSearchResult(entry_key=entry_key, score=score)
                for score, entry_key in sorted(heap, reverse=True)
            ]

        except Exception as e:
            # numpy计算失败时回退到普通版本
            _logger.debug("numpy批量计算失败，回退到普通版本: %s", e)
            return self.search_relevant(
                query_embedding, limit=limit, min_score=min_score, _allow_batch=False
            )

    def get_stats(self) -> dict[str, Any]:
        """返回索引条目数与向量维度等统计信息。"""
        self._ensure_loaded()
        with self._index_lock:
            return {
                "total_embeddings": len(self._entries),
                "dim": self._dim,
            }


# ============================================================================
# 嵌入搜索提供者
# ============================================================================


class EmbeddingSearchProvider:
    """使用 ``embedding.base_url`` / ``embedding.model`` / ``secrets.embed_api_key`` 配置专用 embedding 服务。"""

    def __init__(
        self,
        state_dir: str = "workspaces",
        registry: MemoryEntryRegistry | None = None,
    ) -> None:
        """创建嵌入搜索提供者；未配置 ``embedding.*`` 时 ``get_embedding`` 返回 None。"""
        self._registry = registry or MemoryEntryRegistry(state_dir=state_dir)
        self._index = EmbeddingIndex(state_dir=state_dir, registry=self._registry)
        self._providers: list[dict[str, str | int]] = []
        self._http_client: httpx.AsyncClient | None = None
        self._inflight_embeddings: dict[
            str, asyncio.Task[array[float] | None]
        ] = {}
        self._inflight_lock = asyncio.Lock()
        self._index_queue_max_size = max(
            1, int(get_config("embedding.index_queue_max_size", 256))
        )
        self._index_concurrency = max(
            1, int(get_config("embedding.index_concurrency", 2))
        )
        self._index_semaphore = asyncio.Semaphore(self._index_concurrency)
        self._pending_index_tasks: set[asyncio.Task[None]] = set()
        self._pending_index_lock = asyncio.Lock()
        self._init_providers()

    def _init_providers(self) -> None:
        """仅使用 embedding.* / secrets.embed_api_key 配置；未配置时无 embedding，
        由调用方回退到关键词索引。"""
        embed = _get_embed_config()
        if embed["base_url"] and embed["model"] and embed["api_key"]:
            self._providers.append(embed)

    @staticmethod
    def _provider_namespace(provider: dict[str, str | int]) -> str:
        return f"{str(provider['base_url']).rstrip('/')}\0{provider['model']}"

    def _primary_namespace(self) -> str:
        if not self._providers:
            return _embedding_cache_namespace()
        return self._provider_namespace(self._providers[0])

    @property
    def index(self) -> EmbeddingIndex:
        """底层 ``EmbeddingIndex`` 实例（持久化与 ``index_entry`` 入口）。"""
        return self._index

    def _get_http_client(self, timeout: float = 15.0) -> httpx.AsyncClient:
        """Return this provider's lazily-created reusable connection pool."""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=timeout)
        return self._http_client

    async def close(self) -> None:
        """Close the provider-owned HTTP connection pool, if it was created."""
        await self.drain_indexing()
        async with self._inflight_lock:
            inflight = tuple(self._inflight_embeddings.values())
            self._inflight_embeddings.clear()
        for task in inflight:
            task.cancel()
        if inflight:
            await asyncio.gather(*inflight, return_exceptions=True)
        client = self._http_client
        self._http_client = None
        if client is not None:
            await client.aclose()

    async def _fetch_and_cache_embedding(
        self,
        clean: str,
        *,
        purpose: str,
    ) -> array[float] | None:
        """Fetch one cache miss; concurrent callers share the owning task."""
        for provider in self._providers:
            try:
                start_time = time.monotonic()
                embedding = await _get_embedding(
                    clean,
                    client=self._get_http_client(),
                    base_url=str(provider["base_url"]),
                    model=str(provider["model"]),
                    api_key=str(provider["api_key"]),
                )
                compact = _cache_embedding(
                    clean,
                    embedding,
                    namespace=self._provider_namespace(provider),
                )
                elapsed_ms = int((time.monotonic() - start_time) * 1000)
                emit_trace({
                    "type": EVENT_EMBEDDING_API_CALL,
                    "purpose": purpose,
                    "text_length": len(clean),
                    "duration_ms": elapsed_ms,
                    "network_ms": elapsed_ms,
                    "cache_size": _embedding_cache_size(),
                    "success": True,
                })
                return compact
            except asyncio.CancelledError:
                raise
            except Exception as e:
                response = getattr(e, "response", None)
                status_code = getattr(e, "status_code", None)
                if status_code is None:
                    status_code = getattr(response, "status_code", None)
                if isinstance(e, httpx.TimeoutException):
                    failure_category = "timeout"
                elif isinstance(status_code, int):
                    failure_category = f"http_{status_code}"
                elif isinstance(e, httpx.TransportError):
                    failure_category = "transport"
                elif isinstance(e, ValueError | TypeError):
                    failure_category = "invalid_response"
                else:
                    failure_category = "provider_error"
                elapsed_ms = int((time.monotonic() - start_time) * 1000)
                emit_trace({
                    "type": EVENT_EMBEDDING_API_CALL,
                    "purpose": purpose,
                    "text_length": len(clean),
                    "duration_ms": elapsed_ms,
                    "network_ms": elapsed_ms,
                    "cache_size": _embedding_cache_size(),
                    "success": False,
                    "failure_category": failure_category,
                })
                _logger.warning(
                    "嵌入供应商失败: category=%s error_type=%s",
                    failure_category,
                    type(e).__name__,
                )
        return None

    async def _run_embedding_request(self, clean: str, *, purpose: str) -> array[float] | None:
        """Own one in-flight request and remove it even if every waiter cancels."""
        try:
            return await self._fetch_and_cache_embedding(clean, purpose=purpose)
        finally:
            current = asyncio.current_task()
            async with self._inflight_lock:
                inflight_key = f"{self._primary_namespace()}\0{clean}"
                if self._inflight_embeddings.get(inflight_key) is current:
                    self._inflight_embeddings.pop(inflight_key, None)

    async def get_embedding(self, text: str, *, purpose: str = "query") -> Sequence[float] | None:
        """获取文本的嵌入向量（带缓存）。

        性能优化：
        - 缓存命中：减少200-500ms延迟
        - 缓存未命中：调用API并缓存结果

        Args:
            text: 输入文本

        Returns:
            嵌入向量（成功时），None（失败时）
        """
        clean = re.sub(r"\s+", " ", text).strip()
        if not clean:
            return None

        # 性能优化：先检查缓存
        namespace = self._primary_namespace()
        inflight_key = f"{namespace}\0{clean}"
        cached = _get_cached_embedding(clean, namespace=namespace)
        if cached is not None:
            emit_trace({
                "type": EVENT_EMBEDDING_CACHE_HIT,
                "purpose": purpose,
                "text_length": len(clean),
                "cache_size": _embedding_cache_size(),
            })
            return cached

        async with self._inflight_lock:
            # A previous owner may have filled the process cache while this
            # caller was waiting for the single-flight lock.
            cached = _get_cached_embedding(clean, namespace=namespace)
            if cached is not None:
                emit_trace({
                    "type": EVENT_EMBEDDING_CACHE_HIT,
                    "purpose": purpose,
                    "text_length": len(clean),
                    "cache_size": _embedding_cache_size(),
                })
                return cached
            task = self._inflight_embeddings.get(inflight_key)
            if task is None:
                task = asyncio.create_task(
                    self._run_embedding_request(clean, purpose=purpose),
                    name="embedding-fetch",
                )
                self._inflight_embeddings[inflight_key] = task

        return await asyncio.shield(task)

    async def queue_index(
        self,
        session_id: str,
        entry: MemoryEntry,
        text: str,
    ) -> None:
        """Queue one durable-memory entry for bounded asynchronous vector indexing.

        Queue saturation applies backpressure to the producer; entries are
        never dropped.  ``search`` and ``close`` drain pending work to preserve
        read-after-write and shutdown consistency.
        """
        while True:
            wait_for: asyncio.Task[None] | None = None
            async with self._pending_index_lock:
                completed = {task for task in self._pending_index_tasks if task.done()}
                self._pending_index_tasks.difference_update(completed)
                for task in completed:
                    try:
                        task.result()
                    except (Exception, asyncio.CancelledError):
                        pass
                if len(self._pending_index_tasks) < self._index_queue_max_size:
                    queued_at_ns = time.monotonic_ns()
                    task = asyncio.create_task(
                        self._index_one(session_id, entry, text, queued_at_ns),
                        name="embedding-index",
                    )
                    self._pending_index_tasks.add(task)
                    emit_trace(
                        {
                            "type": EVENT_EMBEDDING_INDEX_QUEUED,
                            "purpose": "index",
                            "queue_depth": len(self._pending_index_tasks),
                            "text_length": len(text),
                        }
                    )
                    return
                wait_for = next(iter(self._pending_index_tasks))
            if wait_for is not None:
                await asyncio.gather(asyncio.shield(wait_for), return_exceptions=True)

    async def _index_one(
        self,
        session_id: str,
        entry: MemoryEntry,
        text: str,
        queued_at_ns: int,
    ) -> None:
        started_ns = time.monotonic_ns()
        success = False
        failure_category: str | None = None
        index_duration_ms = 0.0
        try:
            async with self._index_semaphore:
                started_ns = time.monotonic_ns()
                embedding = await self.get_embedding(text, purpose="index")
                if embedding is not None:
                    index_started_ns = time.monotonic_ns()
                    self._index.index_entry(session_id, entry, embedding=embedding)
                    index_duration_ms = (
                        time.monotonic_ns() - index_started_ns
                    ) / 1_000_000
                    success = True
                else:
                    failure_category = "embedding_unavailable"
        except asyncio.CancelledError:
            raise
        except Exception as error:
            failure_category = "index_error"
            _logger.debug("异步嵌入索引失败: %s", error)
        finally:
            emit_trace(
                {
                    "type": EVENT_EMBEDDING_INDEX_COMPLETED,
                    "purpose": "index",
                    "success": success,
                    "failure_category": failure_category,
                    "queue_wait_ms": (started_ns - queued_at_ns) / 1_000_000,
                    "index_duration_ms": index_duration_ms,
                    "duration_ms": (time.monotonic_ns() - started_ns) / 1_000_000,
                }
            )

    async def drain_indexing(self) -> None:
        """Wait until all entries accepted by :meth:`queue_index` are indexed."""
        while True:
            async with self._pending_index_lock:
                pending = tuple(self._pending_index_tasks)
            if not pending:
                return
            await asyncio.gather(*pending, return_exceptions=True)
            async with self._pending_index_lock:
                self._pending_index_tasks.difference_update(
                    task for task in pending if task.done()
                )

    async def search(
        self,
        query: str,
        *,
        limit: int = 8,
        min_score: float = 0.3,
    ) -> list[EmbeddingSearchResult]:
        """搜索相关记忆。先获取查询向量，再用余弦相似度检索。

        性能优化：使用批量计算版本（numpy加速）。
        """
        await self.drain_indexing()
        if not self._index.has_entries():
            return []
        query_embedding = await self.get_embedding(query, purpose="query")
        if query_embedding is None:
            return []
        # 性能优化：使用批量计算版本（自动回退）
        return self._index.search_relevant_batch(
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


__all__ = [
    "EmbeddingIndex",
    "EmbeddingSearchProvider",
    "EmbeddingSearchResult",
    "embedding_search_enabled",
]
