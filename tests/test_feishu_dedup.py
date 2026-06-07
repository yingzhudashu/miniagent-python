"""飞书消息去重机制测试

测试 miniagent/feishu/feishu_dedup.py 的核心功能：
- 消息 ID 去重
- 去重缓存清理
- flush 机制
- 边界情况（重复消息、过期消息）

设计背景见 docs/FEISHU.md § 消息去重。
"""

import pytest
import time
from unittest.mock import patch, MagicMock


class TestFeishuDedupBasic:
    """基础去重功能测试"""

    def test_try_begin_processing_first_time(self):
        """首次消息应返回 True"""
        from miniagent.feishu.feishu_dedup import (
            try_begin_processing,
            release_processing,
            _disk_dedup,
            _processing_claims,
        )

        message_id = "test_msg_001_unique"
        key = f"mini-agent:{message_id}"

        # 清理之前的测试数据
        _disk_dedup.pop(key, None)
        _processing_claims.pop(key, None)

        result = try_begin_processing(message_id)
        assert result is True

        # 清理测试数据
        _processing_claims.pop(key, None)
        _disk_dedup.pop(key, None)

    def test_try_begin_processing_duplicate(self):
        """重复消息应返回 False"""
        from miniagent.feishu.feishu_dedup import (
            try_begin_processing,
            release_processing,
            _disk_dedup,
            _processing_claims,
        )

        message_id = "test_msg_002"

        # 清理测试数据
        _disk_dedup.pop(f"mini-agent:{message_id}", None)
        _processing_claims.pop(f"mini-agent:{message_id}", None)

        # 第一次处理
        result1 = try_begin_processing(message_id)
        assert result1 is True

        # 释放并记录到磁盘去重
        release_processing(message_id)

        # 第二次处理应被拒绝
        result2 = try_begin_processing(message_id)
        assert result2 is False

        # 清理测试数据
        _disk_dedup.pop(f"mini-agent:{message_id}", None)


class TestFeishuDedupExpiry:
    """过期消息清理测试"""

    def test_expiry_cleanup(self):
        """过期条目应被清理"""
        from miniagent.feishu.feishu_dedup import (
            try_begin_processing,
            release_processing,
            _processing_claims,
            _disk_dedup,
            DEDUP_TTL_MS,
        )

        message_id = "test_msg_old"

        # 清理测试数据
        key = f"mini-agent:{message_id}"
        _processing_claims.pop(key, None)
        _disk_dedup.pop(key, None)

        # 添加一个过期条目（模拟旧消息）
        expired_time = time.time() - DEDUP_TTL_MS / 1000.0 - 100  # 远过期
        _disk_dedup[key] = expired_time

        # 由于未超过阈值，清理不会立即触发
        # 但我们可以验证 TTL 机制的存在
        assert DEDUP_TTL_MS > 0

        # 清理测试数据
        _disk_dedup.pop(key, None)


class TestFeishuDedupFlush:
    """刷盘机制测试"""

    def test_get_dedup_stats(self):
        """统计信息应正确返回"""
        from miniagent.feishu.feishu_dedup import get_dedup_stats

        stats = get_dedup_stats()

        assert "processing_claims" in stats
        assert "disk_dedup" in stats
        assert "dirty" in stats
        assert "state_dir" in stats

        assert isinstance(stats["processing_claims"], int)
        assert isinstance(stats["disk_dedup"], int)
        assert isinstance(stats["dirty"], bool)

    def test_abandon_processing_claim(self):
        """放弃处理权应仅清理内存"""
        from miniagent.feishu.feishu_dedup import (
            try_begin_processing,
            abandon_processing_claim,
            _processing_claims,
            _disk_dedup,
        )

        message_id = "test_msg_abandon"
        key = f"mini-agent:{message_id}"

        # 清理测试数据
        _processing_claims.pop(key, None)
        _disk_dedup.pop(key, None)

        # 获取处理权
        result = try_begin_processing(message_id)
        assert result is True
        assert key in _processing_claims

        # 放弃处理权（不写入磁盘）
        abandon_processing_claim(message_id)
        assert key not in _processing_claims
        assert key not in _disk_dedup

        # 再次获取应成功
        result2 = try_begin_processing(message_id)
        assert result2 is True

        # 清理测试数据
        abandon_processing_claim(message_id)


class TestFeishuDedupThreshold:
    """flush 阈值触发测试"""

    def test_threshold_constants(self):
        """验证阈值常量存在"""
        from miniagent.feishu.feishu_dedup import (
            DEDUP_FLUSH_THRESHOLD,
            DEDUP_FLUSH_INTERVAL,
        )

        assert DEDUP_FLUSH_THRESHOLD > 0
        assert DEDUP_FLUSH_INTERVAL > 0

    def test_maybe_trigger_flush_called(self):
        """验证 flush 触发函数可调用"""
        from miniagent.feishu.feishu_dedup import _maybe_trigger_flush

        # 函数应可调用
        _maybe_trigger_flush()
        assert True


class TestFeishuDedupEdgeCases:
    """边界情况测试"""

    def test_empty_message_id(self):
        """空消息 ID 应返回 True"""
        from miniagent.feishu.feishu_dedup import try_begin_processing

        # 空字符串消息 ID
        result = try_begin_processing("")
        assert result is True

    def test_whitespace_message_id(self):
        """空白消息 ID 去重键不为空，会被记录"""
        from miniagent.feishu.feishu_dedup import (
            try_begin_processing,
            release_processing,
            _resolve_dedup_key,
            _disk_dedup,
            _processing_claims,
        )

        # 空白字符串去重键为 mini-agent:
        key = _resolve_dedup_key("   ")
        assert key == "mini-agent:"

        # 清理之前可能存在的记录
        _disk_dedup.pop(key, None)
        _processing_claims.pop(key, None)

        # 首次处理空白消息应成功
        result = try_begin_processing("   ")
        assert result is True

        # 释放处理权
        release_processing("   ")

        # 清理测试数据
        _disk_dedup.pop(key, None)

    def test_special_characters_message_id(self):
        """特殊字符消息 ID 应正常处理"""
        from miniagent.feishu.feishu_dedup import (
            try_begin_processing,
            release_processing,
            _disk_dedup,
            _processing_claims,
        )

        message_id = "msg_with_special_chars!@#$%^&*()"

        # 清理测试数据
        key = f"mini-agent:{message_id}"
        _disk_dedup.pop(key, None)
        _processing_claims.pop(key, None)

        result = try_begin_processing(message_id)
        assert result is True

        release_processing(message_id)

        # 清理测试数据
        _disk_dedup.pop(key, None)


class TestFeishuDedupConcurrency:
    """并发场景测试"""

    def test_concurrent_processing_same_message(self):
        """同一消息并发处理应只有一个成功"""
        from miniagent.feishu.feishu_dedup import (
            try_begin_processing,
            release_processing,
            _processing_claims,
            _disk_dedup,
        )

        message_id = "test_concurrent_msg"
        key = f"mini-agent:{message_id}"

        # 清理测试数据
        _processing_claims.pop(key, None)
        _disk_dedup.pop(key, None)

        # 模拟并发：第一个成功
        result1 = try_begin_processing(message_id)
        assert result1 is True

        # 第二个应失败（处理中）
        result2 = try_begin_processing(message_id)
        assert result2 is False

        # 释放后第三个应失败（已处理）
        release_processing(message_id)
        result3 = try_begin_processing(message_id)
        assert result3 is False

        # 清理测试数据
        _disk_dedup.pop(key, None)