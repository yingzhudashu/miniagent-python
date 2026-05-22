"""miniagent/memory/embedding_search.py 的单元测试。

嵌入搜索：余弦相似度、多供应商回退、索引持久化、开关控制。
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
from miniagent.types.memory import MemoryEntryInput

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
        return EmbeddingIndex(state_dir=tmpdir)

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
        assert results[0].session_id == "sess-1"
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

        idx2 = EmbeddingIndex(state_dir=str(tmp_path))
        idx2._load()
        assert len(idx2._entries) == 1
        assert idx2._entries["sess-1:2026-05-22T10:00:00Z"].user_snippet == "持久化测试"

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
        assert idx._entries["sess-1:2026-05-22T10:00:00Z"].user_snippet == "修改后的文本"

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
        """未配置 API 时 provider 无供应商。"""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        monkeypatch.delenv("OPENAI_MODEL", raising=False)
        monkeypatch.delenv("MINIAGENT_EMBED_BASE_URL", raising=False)
        monkeypatch.delenv("MINIAGENT_EMBED_MODEL", raising=False)
        reset_embed_provider()

        provider = EmbeddingSearchProvider(state_dir=str(tmp_path))
        assert len(provider._providers) == 0

    def test_primary_provider(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.setenv("OPENAI_BASE_URL", "https://api.example.com/v1")
        monkeypatch.setenv("OPENAI_MODEL", "text-embedding-3-small")
        monkeypatch.delenv("MINIAGENT_EMBED_BASE_URL", raising=False)
        monkeypatch.delenv("MINIAGENT_EMBED_MODEL", raising=False)
        reset_embed_provider()

        provider = EmbeddingSearchProvider(state_dir=str(tmp_path))
        assert len(provider._providers) == 1
        assert provider._providers[0]["api_key"] == "test-key"

    def test_fallback_provider(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "key1")
        monkeypatch.setenv("OPENAI_BASE_URL", "https://primary.com/v1")
        monkeypatch.setenv("OPENAI_MODEL", "model-a")
        monkeypatch.setenv("MINIAGENT_EMBED_BASE_URL", "https://fallback.com/v1")
        monkeypatch.setenv("MINIAGENT_EMBED_MODEL", "model-b")
        reset_embed_provider()

        provider = EmbeddingSearchProvider(state_dir=str(tmp_path))
        assert len(provider._providers) == 2

    def test_same_url_no_duplicate_provider(self, tmp_path, monkeypatch):
        """备用与主供应商相同则不重复添加。"""
        monkeypatch.setenv("OPENAI_API_KEY", "key1")
        monkeypatch.setenv("OPENAI_BASE_URL", "https://same.com/v1")
        monkeypatch.setenv("OPENAI_MODEL", "model-a")
        monkeypatch.setenv("MINIAGENT_EMBED_BASE_URL", "https://same.com/v1")
        monkeypatch.setenv("MINIAGENT_EMBED_MODEL", "model-a")
        reset_embed_provider()

        provider = EmbeddingSearchProvider(state_dir=str(tmp_path))
        assert len(provider._providers) == 1


# ============================================================================
# 开关
# ============================================================================

class TestEmbeddingSearchEnabled:
    def test_default_disabled(self, monkeypatch):
        monkeypatch.delenv("MINIAGENT_EMBED_SEARCH", raising=False)
        assert embedding_search_enabled() is False

    def test_explicit_enabled(self, monkeypatch):
        for val in ("1", "true", "yes", "on"):
            monkeypatch.setenv("MINIAGENT_EMBED_SEARCH", val)
            assert embedding_search_enabled() is True

    def test_disabled(self, monkeypatch):
        for val in ("0", "false", "no", "off"):
            monkeypatch.setenv("MINIAGENT_EMBED_SEARCH", val)
            assert embedding_search_enabled() is False
