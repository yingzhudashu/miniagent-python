"""miniagent/memory/embedding_search.py 的单元测试。

嵌入搜索：余弦相似度、索引持久化、开关控制。
不依赖真实 API（全部 mock）。
"""

from __future__ import annotations

import os

import pytest

from miniagent.memory.embedding_search import (
    EmbeddingIndex,
    EmbeddingSearchProvider,
    _cosine_similarity,
    _text_hash,
    embedding_search_enabled,
    reset_embed_provider,
)
from miniagent.memory.shared_registry import MemoryEntryRegistry, reset_registry
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


# ============================================================================
# 嵌入搜索提供者
# ============================================================================


class TestEmbeddingSearchProvider:
    def test_no_providers_without_config(self, tmp_path, monkeypatch):
        """未配置 embedding 端点时无供应商，回退到关键词索引。"""
        install_test_config(tmp_path, {})
        monkeypatch.delenv("MINIAGENT_EMBED_API_KEY", raising=False)
        reset_embed_provider()
        reset_registry()

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
        reset_embed_provider()
        reset_registry()

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
        reset_embed_provider()
        reset_registry()

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
