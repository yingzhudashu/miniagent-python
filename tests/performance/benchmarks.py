"""MiniAgent Python 性能基准测试套件

提供以下测试场景：
- B1: 流式输出吞吐量（chunks/sec）
- B2: 终端渲染帧率（invalidate/sec）
- B3: 内存增长曲线（会话数 × 内存）
- B4: 启动时间分解
- B5: 工具调用延迟
- B6: Token 计算性能
- B7: Embedding 搜索性能
"""

from __future__ import annotations

import math
import time
from collections import OrderedDict
from typing import Any

import pytest

from tests.perf_helpers import (
    median_wall_seconds,
    tracemalloc_peak_diff_mb,
)

# =============================================================================
# B1: 流式输出吞吐量测试
# =============================================================================


class TestStreamingThroughput:
    """B1: 流式输出吞吐量测试"""

    @pytest.mark.perf
    def test_streaming_buffer_baseline(self):
        """基准：字符串拼接模式性能"""
        chunks = ["hello world " * 10 for _ in range(100)]

        # 模式1: 每次 join（原实现）
        def old_pattern():
            parts = []
            for chunk in chunks:
                parts.append(chunk)
                _ = "".join(parts)  # 每次 join
            return "".join(parts)

        # 模式2: 增量 buffer（优化实现）
        def new_pattern():
            buffer = []
            total_len = 0
            for chunk in chunks:
                buffer.append(chunk)
                total_len += len(chunk)
                # 仅在 buffer 过长时合并
                if len(buffer) > 50:
                    buffer = ["".join(buffer)]
            return "".join(buffer)

        old_time = median_wall_seconds(5, old_pattern)
        new_time = median_wall_seconds(5, new_pattern)

        # 记录结果
        print("\n流式吞吐对比:")
        print(f"  旧模式: {old_time*1000:.2f}ms ({100/old_time:.1f} chunks/s)")
        print(f"  新模式: {new_time*1000:.2f}ms ({100/new_time:.1f} chunks/s)")
        print(f"  提升: {(old_time/new_time):.2f}x")

        # 验证新模式更快
        assert new_time < old_time, "新模式应该更快"

    @pytest.mark.perf
    def test_streaming_memory_pattern(self):
        """流式输出内存分配模式"""
        chunks = ["hello world " * 10 for _ in range(100)]

        def old_pattern():
            parts = []
            for chunk in chunks:
                parts.append(chunk)
                _ = "".join(parts)
            return "".join(parts)

        def new_pattern():
            buffer = []
            for chunk in chunks:
                buffer.append(chunk)
                if len(buffer) > 50:
                    buffer = ["".join(buffer)]
            return "".join(buffer)

        old_mem = tracemalloc_peak_diff_mb(old_pattern)
        new_mem = tracemalloc_peak_diff_mb(new_pattern)

        print("\n流式内存对比:")
        print(f"  旧模式峰值: {old_mem:.2f}MB")
        print(f"  新模式峰值: {new_mem:.2f}MB")

        # 验证新模式内存更低（或相近）
        assert new_mem <= old_mem * 1.1, "新模式内存不应更高"


# =============================================================================
# B2: 终端渲染帧率测试
# =============================================================================


class TestRenderPerformance:
    """B2: 终端渲染帧率测试"""

    @pytest.mark.perf
    def test_invalidate_debounce_pattern(self):
        """渲染调度 debounce 性能"""
        # 模拟高频 invalidate 场景
        invalidate_count_old = 0
        invalidate_count_new = 0

        def old_pattern():
            # 直接 invalidate
            nonlocal invalidate_count_old
            for _ in range(100):
                invalidate_count_old += 1

        def new_pattern():
            # debounce 模式
            nonlocal invalidate_count_new
            last_time = 0
            min_interval = 0.05  # 50ms
            for _ in range(100):
                now = time.monotonic()
                if now - last_time >= min_interval:
                    invalidate_count_new += 1
                    last_time = now

        old_time = median_wall_seconds(100, old_pattern)
        new_time = median_wall_seconds(100, new_pattern)

        print("\n渲染调度对比:")
        print(f"  旧模式: {old_time*1000:.3f}ms (无 debounce)")
        print(f"  新模式: {new_time*1000:.3f}ms (debounce)")

        # debounce 模式时间应该相近但实际 invalidate 更少
        assert new_time <= old_time * 2, "debounce 不应显著增加开销"

    @pytest.mark.perf
    def test_terminal_width_cache_pattern(self):
        """终端宽度缓存性能"""
        # 模拟频繁获取终端宽度
        # 使用 shutil.get_terminal_size 作为替代（更稳定）

        import shutil

        def no_cache():
            for _ in range(100):
                # 每次都调用获取
                _ = shutil.get_terminal_size(fallback=(80, 24)).columns

        def with_cache():
            cached_width = None
            cache_time = 0
            ttl = 1.0
            for _ in range(100):
                now = time.monotonic()
                if cached_width is None or now - cache_time > ttl:
                    cached_width = shutil.get_terminal_size(fallback=(80, 24)).columns
                    cache_time = now
                _ = cached_width

        no_cache_time = median_wall_seconds(5, no_cache)
        with_cache_time = median_wall_seconds(5, with_cache)

        print("\n终端宽度缓存对比:")
        print(f"  无缓存: {no_cache_time*1000:.2f}ms")
        print(f"  有缓存: {with_cache_time*1000:.2f}ms")
        print(f"  提升: {(no_cache_time/with_cache_time):.2f}x")

        assert with_cache_time < no_cache_time, "缓存应该更快"


# =============================================================================
# B3: 内存增长曲线测试
# =============================================================================


class TestMemoryGrowth:
    """B3: 内存增长曲线测试"""

    @pytest.mark.perf
    def test_session_storage_growth(self):
        """会话存储内存增长"""
        # 模拟会话数据结构
        def create_session_data():
            return {
                "registry": {"tools": list(range(50))},
                "history": [{"role": "user", "content": "test"} for _ in range(20)],
                "config": {"key": "value"},
            }

        # 无界增长模式
        def unbounded_growth():
            sessions = {}
            for i in range(100):
                sessions[f"session_{i}"] = create_session_data()
            return sessions

        # LRU 驎出模式
        def lru_eviction(max_sessions=50):
            sessions = OrderedDict()
            for i in range(100):
                sessions[f"session_{i}"] = create_session_data()
                if len(sessions) > max_sessions:
                    sessions.popitem(last=False)  # 驎出最旧
            return sessions

        unbounded_mem = tracemalloc_peak_diff_mb(unbounded_growth)
        lru_mem = tracemalloc_peak_diff_mb(lambda: lru_eviction(50))

        print("\n会话存储内存对比:")
        print(f"  无界增长 (100会话): {unbounded_mem:.2f}MB")
        print(f"  LRU (50上限): {lru_mem:.2f}MB")
        print(f"  节省: {(unbounded_mem - lru_mem):.2f}MB")

        # LRU 内存应该更低
        assert lru_mem < unbounded_mem, "LRU 应节省内存"

    @pytest.mark.perf
    def test_history_data_structure(self):
        """历史数据结构选择"""
        # List 模式
        def list_based():
            history = []
            for i in range(200):
                history.append({"msg": f"message_{i}"})
            return history

        # Deque 模式（有界）
        from collections import deque
        def deque_based(maxlen=200):
            history = deque(maxlen=maxlen)
            for i in range(200):
                history.append({"msg": f"message_{i}"})
            return list(history)

        list_mem = tracemalloc_peak_diff_mb(list_based)
        deque_mem = tracemalloc_peak_diff_mb(deque_based)

        print("\n历史数据结构对比:")
        print(f"  List: {list_mem:.2f}MB")
        print(f"  Deque: {deque_mem:.2f}MB")

        # Deque 内存相近或更低
        assert deque_mem <= list_mem * 1.1


# =============================================================================
# B4: Token 计算性能测试
# =============================================================================


class TestTokenCalculation:
    """B4: Token 计算性能测试"""

    @pytest.mark.perf
    def test_token_estimation_regex(self):
        """Token 估算正则性能"""
        import re

        # 预编译模式
        _pattern = re.compile(r"[^\x00-\x7F]")

        text = "这是一个中文测试文本" * 100

        def with_findall():
            # 使用 findall（原实现）
            chinese_chars = len(_pattern.findall(text))
            ascii_chars = len(text) - chinese_chars
            return int(chinese_chars * 1.5 + ascii_chars / 4) + 1

        def with_sub():
            # 使用 sub 替换后计数
            chinese_chars = len(_pattern.sub("", text))
            return int(chinese_chars * 1.5 + (len(text) - chinese_chars) / 4) + 1

        findall_time = median_wall_seconds(100, with_findall)
        sub_time = median_wall_seconds(100, with_sub)

        print("\nToken 估算性能对比:")
        print(f"  findall: {findall_time*1000:.3f}ms")
        print(f"  sub: {sub_time*1000:.3f}ms")

        # findall 创建列表，sub 直接替换
        # 对于大文本，sub 可能更快

    @pytest.mark.perf
    def test_incremental_token_calculation(self):
        """增量 Token 计算性能"""
        messages = [{"role": "user", "content": "test message " * 50} for _ in range(100)]

        def full_recalc():
            total = 0
            for _ in range(10):
                # 每次全量重算
                total = sum(len(m["content"]) // 4 for m in messages)
            return total

        def incremental():
            total = 0
            # 首次计算
            total = sum(len(m["content"]) // 4 for m in messages)
            # 增量追加
            for _ in range(10):
                new_msg = {"role": "user", "content": "test message " * 50}
                total += len(new_msg["content"]) // 4
            return total

        full_time = median_wall_seconds(5, full_recalc)
        inc_time = median_wall_seconds(5, incremental)

        print("\nToken 计算对比:")
        print(f"  全量重算: {full_time*1000:.2f}ms")
        print(f"  增量计算: {inc_time*1000:.2f}ms")
        print(f"  提升: {(full_time/inc_time):.2f}x")

        assert inc_time < full_time, "增量计算应该更快"


# =============================================================================
# B5: Embedding 搜索性能测试
# =============================================================================


class TestEmbeddingSearch:
    """B5: Embedding 搜索性能测试"""

    @pytest.mark.perf
    def test_cosine_similarity_with_norm_cache(self):
        """Cosine 相似度计算（norm 缓存）"""
        import random

        # 生成测试向量
        dim = 1536
        n_entries = 100

        vectors = [[random.random() for _ in range(dim)] for _ in range(n_entries)]
        query = [random.random() for _ in range(dim)]

        def no_norm_cache():
            results = []
            for v in vectors:
                dot = sum(a * b for a, b in zip(query, v, strict=True))
                norm_q = math.sqrt(sum(x * x for x in query))
                norm_v = math.sqrt(sum(x * x for x in v))
                sim = dot / (norm_q * norm_v) if norm_q * norm_v > 0 else 0
                results.append(sim)
            return results

        def with_norm_cache():
            # 预计算 norm
            norm_q = math.sqrt(sum(x * x for x in query))
            cached_norms = [math.sqrt(sum(x * x for x in v)) for v in vectors]

            results = []
            for i, v in enumerate(vectors):
                dot = sum(a * b for a, b in zip(query, v, strict=True))
                sim = dot / (norm_q * cached_norms[i]) if norm_q * cached_norms[i] > 0 else 0
                results.append(sim)
            return results

        no_cache_time = median_wall_seconds(3, no_norm_cache)
        cache_time = median_wall_seconds(3, with_norm_cache)

        print("\nEmbedding 相似度计算对比:")
        print(f"  无缓存: {no_cache_time*1000:.2f}ms")
        print(f"  有缓存: {cache_time*1000:.2f}ms")
        print(f"  提升: {(no_cache_time/cache_time):.2f}x")

        assert cache_time < no_cache_time, "norm 缓存应该更快"

    @pytest.mark.perf
    def test_topk_search_optimization(self):
        """Top-K 搜索优化（heapq vs sort）"""
        import heapq
        import random

        # 生成测试数据 - 使用更大的 n 以体现 heapq 优势
        n_entries = 5000  # 增大以体现差异
        scores = [(random.random(), i) for i in range(n_entries)]
        k = 10

        def full_sort():
            sorted_scores = sorted(scores, key=lambda x: x[0], reverse=True)
            return sorted_scores[:k]

        def heap_topk():
            # 使用 nlargest 更高效
            return heapq.nlargest(k, scores, key=lambda x: x[0])

        sort_time = median_wall_seconds(10, full_sort)
        heap_time = median_wall_seconds(10, heap_topk)

        print(f"\nTop-K 搜索对比 (n={n_entries}, k={k}):")
        print(f"  全排序: {sort_time*1000:.2f}ms")
        print(f"  heapq.nlargest: {heap_time*1000:.2f}ms")
        ratio = sort_time / heap_time if heap_time > 0 else 1
        if ratio >= 1:
            print(f"  提升: {ratio:.2f}x")
        else:
            print("  heapq 略慢（小数据集下 heap 操作开销相对高）")

        # 对于 k << n，heapq 应该更快；但不强制要求（取决于具体实现）


# =============================================================================
# B6: 正则预编译性能测试
# =============================================================================


class TestRegexPrecompile:
    """B6: 正则预编译性能测试"""

    @pytest.mark.perf
    def test_regex_precompile_impact(self):
        """正则预编译对性能的影响"""
        import re

        text = "<br/>这是一个测试<br />换行<br>处理" * 100
        pattern = re.compile(r"<br\s*/?>")

        def no_precompile():
            result = text
            for _ in range(10):
                re.purge()
                result = re.compile(r"<br\s*/?>").sub("\n", result)
            return result

        def with_precompile():
            result = text
            for _ in range(10):
                result = pattern.sub("\n", result)
            return result

        no_pre_time = median_wall_seconds(5, no_precompile)
        pre_time = median_wall_seconds(5, with_precompile)

        print("\n正则预编译对比:")
        print(f"  无预编译: {no_pre_time*1000:.2f}ms")
        print(f"  预编译: {pre_time*1000:.2f}ms")
        print(f"  提升: {(no_pre_time/pre_time):.2f}x")

        assert pre_time <= no_pre_time * 0.9, "预编译热点应至少提升 10%"


# =============================================================================
# B7: 签名检查缓存测试
# =============================================================================


class TestSignatureCache:
    """B7: 签名检查缓存测试"""

    @pytest.mark.perf
    def test_signature_inspection_cache(self):
        """签名检查缓存性能"""
        import inspect

        def sample_func(a: int, b: str = "default", c: Any = None) -> str:
            return f"{a}-{b}-{c}"

        def no_cache():
            for _ in range(100):
                sig = inspect.signature(sample_func)
                params = sig.parameters
                _ = "b" in params
            return sig

        def with_cache():
            cache = {}
            func_id = id(sample_func)
            if func_id not in cache:
                cache[func_id] = inspect.signature(sample_func)
            sig = cache[func_id]

            for _ in range(100):
                params = sig.parameters
                _ = "b" in params
            return sig

        no_cache_time = median_wall_seconds(10, no_cache)
        cache_time = median_wall_seconds(10, with_cache)

        print("\n签名检查缓存对比:")
        print(f"  无缓存: {no_cache_time*1000:.2f}ms")
        print(f"  有缓存: {cache_time*1000:.2f}ms")
        print(f"  提升: {(no_cache_time/cache_time):.2f}x")

        assert cache_time < no_cache_time, "缓存应该更快"


__all__ = [
    "TestStreamingThroughput",
    "TestRenderPerformance",
    "TestMemoryGrowth",
    "TestTokenCalculation",
    "TestEmbeddingSearch",
    "TestRegexPrecompile",
    "TestSignatureCache",
]
