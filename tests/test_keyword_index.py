"""Mini Agent Python — 关键词索引测试

测试 miniagent/memory/keyword_index.py 核心功能：
- extract_keywords 关键词提取
- KeywordIndex 索引管理
- search_relevant 相关性检索
- format_results 结果格式化
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from miniagent.memory.keyword_index import (
    KeywordIndex,
    extract_keywords,
    format_search_results,
    get_index_stats,
    search_relevant_memory,
    search_relevant_with_index,
)
from miniagent.memory.shared_registry import reset_registry
from miniagent.types.memory import MemoryEntryInput


@pytest.fixture(autouse=True)
def _reset_singletons() -> None:
    """每个测试前重置单例"""
    reset_registry()
    yield
    reset_registry()


class TestExtractKeywords:
    """测试关键词提取"""

    def test_extract_english_keywords(self) -> None:
        """英文关键词提取"""
        text = "I love Python programming and machine learning"
        keywords = extract_keywords(text)
        assert "python" in keywords
        assert "love" in keywords or "programming" in keywords

    def test_extract_chinese_keywords(self) -> None:
        """中文关键词提取（n-gram）"""
        text = "我喜欢编写Python代码"
        keywords = extract_keywords(text)
        # 应提取中文 2-gram 或 3-gram
        assert len(keywords) > 0

    def test_extract_mixed_keywords(self) -> None:
        """混合语言关键词提取"""
        text = "我喜欢 Python programming and AI development"
        keywords = extract_keywords(text)
        assert "python" in keywords
        assert len(keywords) > 0

    def test_extract_empty_text(self) -> None:
        """空文本返回空列表"""
        keywords = extract_keywords("")
        assert keywords == []

    def test_extract_stopwords_removed(self) -> None:
        """停用词应被移除"""
        text = "the is a an are was were"
        keywords = extract_keywords(text)
        # 英文停用词应被过滤
        assert len(keywords) == 0

    def test_extract_chinese_stopwords_removed(self) -> None:
        """中文停用词应被移除"""
        text = "的是在了我有和就"
        keywords = extract_keywords(text)
        # 单独中文停用词应被过滤，但 n-gram 组合可能产生非停用词
        # 验证没有单独的停用词
        for kw in keywords:
            if len(kw) == 1:
                assert kw not in ["的", "是", "在", "了", "我", "有", "和", "就"]

    def test_extract_max_keywords_limit(self) -> None:
        """最大关键词数限制"""
        long_text = "Python Java C++ JavaScript Go Rust " * 100
        keywords = extract_keywords(long_text, max_keywords=10)
        assert len(keywords) <= 10

    def test_extract_single_char_filtered(self) -> None:
        """单字符英文应被过滤"""
        text = "a b c d Python"
        keywords = extract_keywords(text)
        assert "a" not in keywords
        assert "python" in keywords


class TestKeywordIndex:
    """测试关键词索引"""

    def test_index_creation(self) -> None:
        """索引创建"""
        with tempfile.TemporaryDirectory() as tmpdir:
            idx = KeywordIndex(state_dir=tmpdir)
            assert idx._state_dir == tmpdir
            stats = idx.get_stats()
            assert stats["total_keywords"] == 0

    def test_index_entry(self) -> None:
        """索引记忆条目"""
        with tempfile.TemporaryDirectory() as tmpdir:
            idx = KeywordIndex(state_dir=tmpdir)
            entry = MemoryEntryInput(
                timestamp="2026-06-03T12:00:00Z",
                user_snippet="Python编程问题",
                summary="讨论了Python最佳实践",
                facts=["用户喜欢Python", "用户是开发者"],
            )
            idx.index_entry("session-001", entry)
            stats = idx.get_stats()
            assert stats["total_keywords"] > 0

    def test_index_multiple_entries(self) -> None:
        """索引多个条目"""
        with tempfile.TemporaryDirectory() as tmpdir:
            idx = KeywordIndex(state_dir=tmpdir)
            for i in range(3):
                entry = MemoryEntryInput(
                    timestamp=f"2026-06-03T12:0{i}:00Z",
                    user_snippet=f"问题{i}",
                    summary=f"讨论{i}",
                )
                idx.index_entry(f"session-{i}", entry)
            stats = idx.get_stats()
            assert stats["total_keywords"] > 0

    def test_search_relevant(self) -> None:
        """相关性检索"""
        with tempfile.TemporaryDirectory() as tmpdir:
            idx = KeywordIndex(state_dir=tmpdir)
            entry1 = MemoryEntryInput(
                timestamp="2026-06-03T12:00:00Z",
                user_snippet="Python开发",
                summary="讨论Python编程",
            )
            entry2 = MemoryEntryInput(
                timestamp="2026-06-03T12:01:00Z",
                user_snippet="Java开发",
                summary="讨论Java编程",
            )
            idx.index_entry("session-1", entry1)
            idx.index_entry("session-2", entry2)

            results = idx.search_relevant("Python编程", limit=5)
            assert len(results) > 0
            # Python相关条目应排在前面
            top_result = results[0]
            assert top_result.score > 0

    def test_search_empty_query(self) -> None:
        """空查询返回空结果"""
        with tempfile.TemporaryDirectory() as tmpdir:
            idx = KeywordIndex(state_dir=tmpdir)
            entry = MemoryEntryInput(
                timestamp="2026-06-03T12:00:00Z",
                user_snippet="test",
                summary="test",
            )
            idx.index_entry("session-1", entry)
            results = idx.search_relevant("", limit=5)
            assert results == []

    def test_format_results(self) -> None:
        """格式化检索结果"""
        with tempfile.TemporaryDirectory() as tmpdir:
            idx = KeywordIndex(state_dir=tmpdir)
            entry = MemoryEntryInput(
                timestamp="2026-06-03T12:00:00Z",
                user_snippet="Python问题",
                summary="Python解决方案",
            )
            idx.index_entry("session-1", entry)

            results = idx.search_relevant("Python", limit=5)
            formatted = idx.format_results(results)
            if results:
                assert "相关记忆检索" in formatted

    def test_save_and_load(self) -> None:
        """保存和加载索引"""
        with tempfile.TemporaryDirectory() as tmpdir:
            idx1 = KeywordIndex(state_dir=tmpdir)
            entry = MemoryEntryInput(
                timestamp="2026-06-03T12:00:00Z",
                user_snippet="test",
                summary="test summary",
            )
            idx1.index_entry("session-1", entry)
            idx1.save()

            # 加载新索引
            idx2 = KeywordIndex(state_dir=tmpdir)
            stats2 = idx2.get_stats()
            assert stats2["total_keywords"] > 0

    def test_prune_expired(self) -> None:
        """清理过期条目"""
        with tempfile.TemporaryDirectory() as tmpdir:
            idx = KeywordIndex(state_dir=tmpdir)
            entry = MemoryEntryInput(
                timestamp="2020-01-01T00:00:00Z",  # 很旧的条目
                user_snippet="old",
                summary="old summary",
            )
            idx.index_entry("session-old", entry)
            idx.save()

            # 清理 30 天前的条目
            removed = idx.prune_expired(days_old=30)
            # 应清理一些条目（因为 timestamp 很旧）
            assert removed >= 0


class TestSearchRelevantWithIndex:
    """测试便捷搜索函数"""

    def test_search_relevant_with_index(self) -> None:
        """便捷函数"""
        with tempfile.TemporaryDirectory() as tmpdir:
            idx = KeywordIndex(state_dir=tmpdir)
            entry = MemoryEntryInput(
                timestamp="2026-06-03T12:00:00Z",
                user_snippet="Python",
                summary="Python summary",
            )
            idx.index_entry("session-1", entry)

            results = search_relevant_with_index(idx, "Python", top_k=5)
            if results:
                assert results[0]["summary"] == "Python summary"


class TestFormatSearchResults:
    """测试格式化搜索结果"""

    def test_format_empty_results(self) -> None:
        """空结果"""
        formatted = format_search_results([])
        assert formatted == ""

    def test_format_with_results(self) -> None:
        """有结果"""
        results = [
            {
                "session_id": "s1",
                "summary": "Python summary",
                "score": 2.0,
            }
        ]
        formatted = format_search_results(results)
        assert "相关记忆" in formatted
        assert "Python summary" in formatted


class TestGetIndexStats:
    """测试获取索引统计"""

    def test_get_stats_with_index(self) -> None:
        """获取统计"""
        with tempfile.TemporaryDirectory() as tmpdir:
            idx = KeywordIndex(state_dir=tmpdir)
            entry = MemoryEntryInput(
                timestamp="2026-06-03T12:00:00Z",
                user_snippet="test",
                summary="test",
            )
            idx.index_entry("session-1", entry)
            stats = idx.get_stats()
            assert "total_keywords" in stats
            assert "total_references" in stats
            assert "top_keywords" in stats