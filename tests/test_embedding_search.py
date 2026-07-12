"""miniagent/memory/embedding_search.py 的单元测试。

嵌入搜索：余弦相似度、索引持久化、开关控制。
不依赖真实 API（全部 mock）。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from array import array

import pytest

from miniagent.memory import embedding_search as embedding_module
from miniagent.memory.embedding_search import (
    _EMBEDDING_CACHE,
    EmbeddingIndex,
    EmbeddingSearchProvider,
    _cache_embedding,
    _cosine_similarity,
    _text_hash,
    embedding_search_enabled,
)
from miniagent.memory.shared_registry import MemoryEntryRegistry
from miniagent.types.memory import MemoryEntryInput
from tests.config_helpers import install_test_config

# ============================================================================
# 余弦相似度
# ============================================================================


class TestCosineSimilarity:
    def test_identical_vectors(self):
        assert _cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        assert _cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_opposite_vectors(self):
        assert _cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)

    def test_empty_vectors(self):
        assert _cosine_similarity([], [1.0, 2.0]) == 0.0

    def test_zero_norm(self):
        assert _cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0


# ============================================================================
# 文本 hash
# ============================================================================


class TestTextHash:
    def test_deterministic(self):
        assert _text_hash("hello") == _text_hash("hello")

    def test_different_inputs(self):
        assert _text_hash("hello") != _text_hash("world")


# ============================================================================
# 嵌入索引
# ============================================================================


class TestEmbeddingIndex:
    def _make_index(self, tmpdir: str) -> EmbeddingIndex:
        registry = MemoryEntryRegistry(state_dir=tmpdir)
        return EmbeddingIndex(state_dir=tmpdir, registry=registry)

    def test_index_and_search(self, tmp_path):
        idx = self._make_index(str(tmp_path))
        entry = MemoryEntryInput(
            timestamp="2026-05-22T10:00:00Z",
            user_snippet="我喜欢吃苹果",
            summary="用户偏好水果",
            facts=["喜欢苹果"],
        )
        # 手动提供嵌入向量（3 维用于测试）
        idx.index_entry("sess-1", entry, embedding=[0.9, 0.1, 0.0])

        query_vec = [0.85, 0.15, 0.0]
        results = idx.search_relevant(query_vec, limit=5, min_score=0.0)
        assert len(results) == 1
        # 结果只包含 entry_key，需要从注册表获取详细信息
        assert results[0].entry_key == "sess-1:2026-05-22T10:00:00Z"
        assert results[0].score == pytest.approx(
            _cosine_similarity([0.9, 0.1, 0.0], [0.85, 0.15, 0.0]),
            abs=1e-6,
        )

    def test_no_results_below_threshold(self, tmp_path):
        idx = self._make_index(str(tmp_path))
        entry = MemoryEntryInput(
            timestamp="2026-05-22T10:00:00Z",
            user_snippet="测试文本",
            summary="摘要",
        )
        idx.index_entry("sess-1", entry, embedding=[1.0, 0.0, 0.0])

        results = idx.search_relevant([0.0, 1.0, 0.0], min_score=0.5)
        assert len(results) == 0

    def test_save_and_load(self, tmp_path):
        idx = self._make_index(str(tmp_path))
        entry = MemoryEntryInput(
            timestamp="2026-05-22T10:00:00Z",
            user_snippet="持久化测试",
            summary="测试保存加载",
        )
        idx.index_entry("sess-1", entry, embedding=[0.5, 0.5, 0.707])
        idx.save()
        idx._registry.save()  # 同时保存注册表

        # 创建新索引并加载
        registry2 = MemoryEntryRegistry(state_dir=str(tmp_path))
        idx2 = EmbeddingIndex(state_dir=str(tmp_path), registry=registry2)
        idx2._load()
        assert len(idx2._entries) == 1
        # 文本内容从注册表获取
        shared = idx2._registry.get("sess-1:2026-05-22T10:00:00Z")
        assert shared is not None
        assert shared.user_snippet == "持久化测试"

    def test_duplicate_skip(self, tmp_path):
        idx = self._make_index(str(tmp_path))
        entry = MemoryEntryInput(
            timestamp="2026-05-22T10:00:00Z",
            user_snippet="重复测试",
            summary="摘要",
        )
        idx.index_entry("sess-1", entry, embedding=[1.0, 0.0, 0.0])
        # 相同内容再次索引应跳过
        idx.index_entry("sess-1", entry, embedding=[0.0, 1.0, 0.0])
        assert len(idx._entries) == 1

    def test_update_on_text_change(self, tmp_path):
        idx = self._make_index(str(tmp_path))
        entry = MemoryEntryInput(
            timestamp="2026-05-22T10:00:00Z",
            user_snippet="原始文本",
            summary="摘要",
        )
        idx.index_entry("sess-1", entry, embedding=[1.0, 0.0, 0.0])

        entry.user_snippet = "修改后的文本"
        idx.index_entry("sess-1", entry, embedding=[0.0, 1.0, 0.0])
        assert len(idx._entries) == 1
        # 文本内容从注册表获取
        shared = idx._registry.get("sess-1:2026-05-22T10:00:00Z")
        assert shared is not None
        assert shared.user_snippet == "修改后的文本"

    def test_get_stats(self, tmp_path):
        idx = self._make_index(str(tmp_path))
        entry = MemoryEntryInput(
            timestamp="2026-05-22T10:00:00Z",
            user_snippet="统计测试",
            summary="摘要",
        )
        idx.index_entry("sess-1", entry, embedding=[1.0, 0.0, 0.0])
        stats = idx.get_stats()
        assert stats["total_embeddings"] == 1
        assert stats["dim"] == 1536  # default

    def test_empty_search(self, tmp_path):
        idx = self._make_index(str(tmp_path))
        results = idx.search_relevant([], limit=5)
        assert len(results) == 0

    def test_no_save_when_not_dirty(self, tmp_path):
        idx = self._make_index(str(tmp_path))
        idx._loaded = True
        idx._dirty = False
        idx.save()  # 不应写磁盘
        assert not os.path.exists(idx._index_file)

    def test_compact_vector_storage_preserves_precision_and_shares_cache(self, tmp_path):
        vector = [float(index) / 1536 for index in range(1536)]
        compact = _cache_embedding("compact-vector-test", vector)
        assert isinstance(compact, array)
        assert compact.typecode == "d"

        idx = self._make_index(str(tmp_path))
        entry = MemoryEntryInput(
            timestamp="2026-05-22T10:00:00Z",
            user_snippet="紧凑向量",
            summary="内存测试",
        )
        idx.index_entry("sess-compact", entry, embedding=compact)
        stored = idx._entries["sess-compact:2026-05-22T10:00:00Z"].embedding

        assert stored is compact
        assert list(stored) == vector
        list_deep_size = sys.getsizeof(vector) + sum(
            sys.getsizeof(value) for value in vector
        )
        assert sys.getsizeof(stored) < list_deep_size / 3
        _EMBEDDING_CACHE.clear()

    def test_chunked_batch_search_matches_scalar_top_k(self, tmp_path, monkeypatch):
        idx = self._make_index(str(tmp_path))
        idx._dim = 32
        for item in range(150):
            vector = [float((item * 7 + dim * 3) % 101) / 101 for dim in range(32)]
            idx.index_entry(
                f"session-{item}",
                MemoryEntryInput(
                    timestamp=str(item),
                    user_snippet=f"entry {item}",
                    summary="batch",
                ),
                embedding=vector,
            )
        query = [float((dim * 11) % 101) / 101 for dim in range(32)]
        monkeypatch.setattr(embedding_module, "_EMBEDDING_BATCH_CHUNK_SIZE", 17)
        numpy = embedding_module._get_numpy()
        observed_batch_sizes: list[int] = []
        if numpy is not None:
            real_asarray = numpy.asarray

            def recording_asarray(values, *args, **kwargs):
                observed_batch_sizes.append(len(values))
                return real_asarray(values, *args, **kwargs)

            monkeypatch.setattr(numpy, "asarray", recording_asarray)

        scalar = idx.search_relevant(
            query,
            limit=12,
            min_score=0.0,
            _allow_batch=False,
        )
        batch = idx.search_relevant_batch(query, limit=12, min_score=0.0)

        assert [result.entry_key for result in batch] == [
            result.entry_key for result in scalar
        ]
        assert [result.score for result in batch] == pytest.approx(
            [result.score for result in scalar],
            abs=1e-6,
        )
        assert idx.search_relevant_batch(query, limit=0) == []
        if numpy is not None:
            assert len(observed_batch_sizes) > 1
            assert max(observed_batch_sizes) <= 17

    def test_save_keeps_dirty_when_index_changes_during_write(
        self,
        tmp_path,
        monkeypatch,
    ):
        idx = self._make_index(str(tmp_path))
        idx.index_entry(
            "session-1",
            MemoryEntryInput(
                timestamp="1",
                user_snippet="first",
                summary="first",
            ),
            embedding=[1.0, 0.0],
        )
        entered = threading.Event()
        release = threading.Event()
        real_dump = embedding_module.atomic_dump_json

        def delayed_dump(*args, **kwargs):
            entered.set()
            assert release.wait(timeout=5)
            return real_dump(*args, **kwargs)

        monkeypatch.setattr(embedding_module, "atomic_dump_json", delayed_dump)
        save_thread = threading.Thread(target=idx.save)
        save_thread.start()
        assert entered.wait(timeout=5)

        idx.index_entry(
            "session-2",
            MemoryEntryInput(
                timestamp="2",
                user_snippet="second",
                summary="second",
            ),
            embedding=[0.0, 1.0],
        )
        release.set()
        save_thread.join(timeout=5)

        assert not save_thread.is_alive()
        assert idx._dirty is True
        idx.save()
        payload = json.loads((tmp_path / "embedding-index.json").read_text("utf-8"))
        assert set(payload["entries"]) == {"session-1:1", "session-2:2"}


# ============================================================================
# 嵌入搜索提供者
# ============================================================================


class TestEmbeddingSearchProvider:
    @pytest.mark.asyncio
    async def test_empty_index_skips_query_embedding_api(self, tmp_path, monkeypatch):
        provider = EmbeddingSearchProvider(state_dir=str(tmp_path))

        async def should_not_run(*args, **kwargs):
            raise AssertionError("empty semantic index must not request an embedding")

        monkeypatch.setattr(provider, "get_embedding", should_not_run)

        assert await provider.search("query") == []
        await provider.close()

    @pytest.mark.asyncio
    async def test_queued_index_is_visible_before_next_search(self, tmp_path, monkeypatch):
        install_test_config(
            tmp_path,
            {"embedding": {"index_queue_max_size": 1, "index_concurrency": 1}},
        )
        provider = EmbeddingSearchProvider(state_dir=str(tmp_path))
        entry = MemoryEntryInput(
            timestamp="2026-07-12T00:00:00+00:00",
            user_snippet="alpha",
            summary="alpha summary",
            facts=[],
        )

        async def fake_embedding(text: str, *, purpose: str = "query"):
            await asyncio.sleep(0)
            return [1.0, 0.0]

        monkeypatch.setattr(provider, "get_embedding", fake_embedding)
        await provider.queue_index("session", entry, "alpha summary")  # type: ignore[arg-type]

        results = await provider.search("alpha", min_score=0.1)

        assert len(results) == 1
        assert provider._pending_index_tasks == set()
        await provider.close()

    @pytest.mark.asyncio
    async def test_concurrent_drains_wait_for_the_same_pending_index(
        self, tmp_path, monkeypatch
    ):
        provider = EmbeddingSearchProvider(state_dir=str(tmp_path))
        entry = MemoryEntryInput(
            timestamp="2026-07-12T00:00:00+00:00",
            user_snippet="alpha",
            summary="alpha summary",
            facts=[],
        )
        started = asyncio.Event()
        release = asyncio.Event()

        async def fake_embedding(text: str, *, purpose: str = "query"):
            started.set()
            await release.wait()
            return [1.0, 0.0]

        monkeypatch.setattr(provider, "get_embedding", fake_embedding)
        await provider.queue_index("session", entry, "alpha summary")  # type: ignore[arg-type]
        first = asyncio.create_task(provider.drain_indexing())
        await started.wait()
        second = asyncio.create_task(provider.drain_indexing())
        await asyncio.sleep(0)

        assert not first.done()
        assert not second.done()

        release.set()
        await asyncio.gather(first, second)
        assert provider._pending_index_tasks == set()
        await provider.close()

    @pytest.mark.asyncio
    async def test_concurrent_identical_misses_share_one_api_request(
        self,
        tmp_path,
        monkeypatch,
    ):
        provider = EmbeddingSearchProvider(state_dir=str(tmp_path))
        provider._providers = [
            {"base_url": "https://embed.example/v1", "model": "embed", "api_key": "test"}
        ]
        calls = 0
        gate = asyncio.Event()

        async def fake_get_embedding(*args, **kwargs):
            nonlocal calls
            calls += 1
            await gate.wait()
            return [0.25, 0.75]

        monkeypatch.setattr(embedding_module, "_get_embedding", fake_get_embedding)
        with embedding_module._EMBEDDING_CACHE_LOCK:
            _EMBEDDING_CACHE.clear()

        tasks = [
            asyncio.create_task(provider.get_embedding("same text"))
            for _ in range(8)
        ]
        await asyncio.sleep(0)
        gate.set()
        results = await asyncio.gather(*tasks)

        assert calls == 1
        assert all(list(result or []) == [0.25, 0.75] for result in results)
        assert provider._inflight_embeddings == {}
        await provider.close()

    @pytest.mark.asyncio
    async def test_cancelled_waiter_does_not_cancel_shared_embedding_request(
        self,
        tmp_path,
        monkeypatch,
    ):
        provider = EmbeddingSearchProvider(state_dir=str(tmp_path))
        provider._providers = [
            {"base_url": "https://embed.example/v1", "model": "embed", "api_key": "test"}
        ]
        started = asyncio.Event()
        release = asyncio.Event()

        async def fake_get_embedding(*args, **kwargs):
            started.set()
            await release.wait()
            return [1.0, 0.0]

        monkeypatch.setattr(embedding_module, "_get_embedding", fake_get_embedding)
        with embedding_module._EMBEDDING_CACHE_LOCK:
            _EMBEDDING_CACHE.clear()

        cancelled = asyncio.create_task(provider.get_embedding("shared cancellation"))
        await started.wait()
        survivor = asyncio.create_task(provider.get_embedding("shared cancellation"))
        await asyncio.sleep(0)
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        release.set()

        assert list(await survivor or []) == [1.0, 0.0]
        assert provider._inflight_embeddings == {}
        await provider.close()

    def test_no_providers_without_config(self, tmp_path, monkeypatch):
        """未配置 embedding 端点时无供应商，回退到关键词索引。"""
        install_test_config(tmp_path, {})
        monkeypatch.delenv("MINIAGENT_EMBED_API_KEY", raising=False)
        registry = MemoryEntryRegistry(state_dir=str(tmp_path))
        provider = EmbeddingSearchProvider(state_dir=str(tmp_path), registry=registry)
        assert len(provider._providers) == 0

    def test_embed_provider_only(self, tmp_path, monkeypatch):
        """配置 embedding.base_url/model 且 API key 时作为唯一供应商。"""
        install_test_config(
            tmp_path,
            {
                "embedding": {
                    "base_url": "https://embed.example.com/v1",
                    "model": "embed-model-v1",
                },
                "secrets": {"embed_api_key": "test-key"},
            },
        )
        registry = MemoryEntryRegistry(state_dir=str(tmp_path))
        provider = EmbeddingSearchProvider(state_dir=str(tmp_path), registry=registry)
        assert len(provider._providers) == 1
        assert provider._providers[0]["base_url"] == "https://embed.example.com/v1"
        assert provider._providers[0]["model"] == "embed-model-v1"

    def test_no_openai_fallback(self, tmp_path, monkeypatch):
        """即使设置了 OPENAI_*，也不会将其作为 embedding 供应商。"""
        install_test_config(tmp_path, {})
        monkeypatch.setenv("OPENAI_API_KEY", "key1")
        monkeypatch.delenv("MINIAGENT_EMBED_API_KEY", raising=False)
        registry = MemoryEntryRegistry(state_dir=str(tmp_path))
        provider = EmbeddingSearchProvider(state_dir=str(tmp_path), registry=registry)
        assert len(provider._providers) == 0


# ============================================================================
# 开关
# ============================================================================


class TestEmbeddingSearchEnabled:
    def test_default_disabled(self, tmp_path):
        install_test_config(tmp_path, {})
        assert embedding_search_enabled() is False

    def test_explicit_enabled(self, tmp_path):
        install_test_config(tmp_path, {"embedding": {"enabled": True}})
        assert embedding_search_enabled() is True

    def test_disabled(self, tmp_path):
        install_test_config(tmp_path, {"embedding": {"enabled": False}})
        assert embedding_search_enabled() is False
