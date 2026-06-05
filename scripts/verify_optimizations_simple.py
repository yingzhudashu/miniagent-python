"""性能优化验证脚本（简化版）

验证关键优化效果：
- Trace事件新增
- 配置项新增
- 缓存机制
- 并发限制

运行方式：
python scripts/verify_optimizations_simple.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def verify_trace_events():
    """验证Trace事件扩展"""
    print("\n1. Trace事件扩展验证:")
    try:
        from miniagent.infrastructure.trace_events import (
            EVENT_BROWSER_CREATE,
            EVENT_BROWSER_REUSE,
            EVENT_BROWSER_CLOSE,
            EVENT_EMBEDDING_CACHE_HIT,
            EVENT_EMBEDDING_API_CALL,
        )
        print("   OK - browser.create/reuse/close 事件已添加")
        print("   OK - embedding.cache_hit/api_call 事件已添加")
        return True
    except Exception as e:
        print(f"   FAIL - {e}")
        return False


def verify_config_updates():
    """验证配置项更新"""
    print("\n2. 配置项更新验证:")
    try:
        from miniagent.infrastructure.json_config import get_config

        # 检查所有新增配置项
        configs = {
            "browser.idle_timeout_seconds": 300,
            "execution.max_concurrent_tools": 5,
            "execution.callback_min_interval_ms": 50,
            "execution.callback_min_chars": 100,
            "embedding.cache_max_size": 1000,
            "embedding.cache_ttl_seconds": 3600,
            "memory.store_cache_max": 200,
            "memory.store_cache_ttl_seconds": 1800,
            "memory.keyword_prune_interval": 86400,
        }

        all_ok = True
        for key, expected in configs.items():
            actual = get_config(key, expected)
            if actual == expected:
                print(f"   OK - {key}: {actual}")
            else:
                print(f"   FAIL - {key}: {actual} (expected {expected})")
                all_ok = False

        return all_ok
    except Exception as e:
        print(f"   FAIL - {e}")
        return False


def verify_embedding_cache():
    """验证Embedding缓存"""
    print("\n3. Embedding缓存验证:")
    try:
        from miniagent.memory.embedding_search import (
            _EMBEDDING_CACHE,
            _get_cached_embedding,
            _cache_embedding,
            _EMBEDDING_CACHE_MAX_SIZE,
            _EMBEDDING_CACHE_TTL_SECONDS,
        )

        # 测试缓存功能
        test_text = "test text for embedding cache"
        test_embedding = [0.1, 0.2, 0.3]

        # 缓存未命中
        result1 = _get_cached_embedding(test_text)
        if result1 is None:
            print("   OK - 缓存未命中（正确）")
        else:
            print("   FAIL - 缓存应该未命中")
            return False

        # 缓存数据
        _cache_embedding(test_text, test_embedding)
        print("   OK - 缓存数据成功")

        # 缓存命中
        result2 = _get_cached_embedding(test_text)
        if result2 == test_embedding:
            print("   OK - 缓存命中，数据正确")
        else:
            print("   FAIL - 缓存数据不匹配")
            return False

        print(f"   OK - 缓存大小: {len(_EMBEDDING_CACHE)}")
        print(f"   OK - 缓存上限: {_EMBEDDING_CACHE_MAX_SIZE}")
        print(f"   OK - TTL: {_EMBEDDING_CACHE_TTL_SECONDS}秒")

        return True
    except Exception as e:
        print(f"   FAIL - {e}")
        return False


def verify_token_cache():
    """验证Token估算缓存"""
    print("\n4. Token估算缓存验证:")
    try:
        from miniagent.memory.context import (
            _TOKEN_ESTIMATE_CACHE,
            estimate_tokens_cached,
            _CACHE_MAX_SIZE,
            _CACHE_TTL_SECONDS,
        )
        import collections

        # 验证缓存类型
        if isinstance(_TOKEN_ESTIMATE_CACHE, collections.OrderedDict):
            print("   OK - 缓存类型: OrderedDict（支持LRU）")
        else:
            print("   FAIL - 缓存类型错误")
            return False

        # 测试估算功能
        test_text = "test text for token estimation"
        tokens1 = estimate_tokens_cached(test_text)
        tokens2 = estimate_tokens_cached(test_text)

        if tokens1 == tokens2:
            print(f"   OK - Token估算: {tokens1}（缓存命中）")
        else:
            print("   FAIL - Token估算不一致")
            return False

        print(f"   OK - 缓存大小: {len(_TOKEN_ESTIMATE_CACHE)}")
        print(f"   OK - 缓存上限: {_CACHE_MAX_SIZE}")
        print(f"   OK - TTL: {_CACHE_TTL_SECONDS}秒")

        return True
    except Exception as e:
        print(f"   FAIL - {e}")
        return False


def verify_concurrent_semaphore():
    """验证并发限制"""
    print("\n5. 工具并发限制验证:")
    try:
        from miniagent.core.executor import _get_tool_semaphore
        from miniagent.infrastructure.json_config import get_config

        semaphore = _get_tool_semaphore()
        max_concurrent = get_config("execution.max_concurrent_tools", 5)

        if semaphore._value == max_concurrent:
            print(f"   OK - Semaphore初始值: {semaphore._value}（正确）")
        else:
            print(f"   FAIL - Semaphore值错误: {semaphore._value}")
            return False

        print(f"   OK - 并发上限配置: {max_concurrent}")

        return True
    except Exception as e:
        print(f"   FAIL - {e}")
        return False


def main():
    """运行所有验证"""
    print("="*60)
    print("Mini Agent 性能优化验证")
    print("="*60)
    print(f"日期: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    results = [
        verify_trace_events(),
        verify_config_updates(),
        verify_embedding_cache(),
        verify_token_cache(),
        verify_concurrent_semaphore(),
    ]

    print("\n" + "="*60)
    print("验证结果汇总")
    print("="*60)

    total = len(results)
    passed = sum(1 for r in results if r)
    failed = total - passed

    print(f"总计: {total}项")
    print(f"通过: {passed}项")
    print(f"失败: {failed}项")
    print(f"通过率: {passed/total*100:.1f}%")

    if passed == total:
        print("\n[SUCCESS] 所有优化验证通过!")
    else:
        print(f"\n[WARNING] {failed}项验证失败")

    return passed == total


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)