"""Tests for miniagent.infrastructure.tracing

验证 trace 钩子系统，包括可选的持久化功能。
"""

import os
import tempfile
from pathlib import Path

import pytest

from miniagent.infrastructure.tracing import (
    auto_register_trace_file_hook,
    clear_trace_hooks,
    emit_trace,
    register_trace_hook,
    unregister_trace_hook,
)


class TestTraceHooks:
    """测试 trace 钩子注册与派发"""

    def setup_method(self) -> None:
        """每个测试前清空钩子"""
        clear_trace_hooks()

    def test_register_and_emit(self) -> None:
        """测试基本钩子注册与事件派发"""
        events: list[dict] = []

        def hook(event: dict) -> None:
            events.append(event)

        register_trace_hook(hook)
        emit_trace({"type": "test", "data": "hello"})
        assert len(events) == 1
        assert events[0]["type"] == "test"

    def test_multiple_hooks(self) -> None:
        """测试多个钩子顺序调用"""
        events1: list[dict] = []
        events2: list[dict] = []

        register_trace_hook(lambda e: events1.append(e))
        register_trace_hook(lambda e: events2.append(e))

        emit_trace({"type": "multi"})
        assert len(events1) == 1
        assert len(events2) == 1

    def test_unregister(self) -> None:
        """测试钩子移除"""
        events: list[dict] = []

        def hook(event: dict) -> None:
            events.append(event)

        register_trace_hook(hook)
        emit_trace({"type": "first"})
        assert len(events) == 1

        unregister_trace_hook(hook)
        emit_trace({"type": "second"})
        assert len(events) == 1  # 没有新事件

    def test_hook_exception_does_not_propagate(self) -> None:
        """测试钩子异常不影响主流程"""
        events: list[dict] = []

        def bad_hook(event: dict) -> None:
            raise RuntimeError("hook error")

        def good_hook(event: dict) -> None:
            events.append(event)

        register_trace_hook(bad_hook)
        register_trace_hook(good_hook)

        # emit_trace 不应抛出异常
        emit_trace({"type": "test"})
        # good_hook 应该仍然被调用
        assert len(events) == 1


class TestTraceFilePersistence:
    """测试 trace 事件持久化到文件"""

    def setup_method(self) -> None:
        """每个测试前清空钩子和环境变量"""
        clear_trace_hooks()
        if "MINIAGENT_TRACE_LOG_FILE" in os.environ:
            del os.environ["MINIAGENT_TRACE_LOG_FILE"]

    def teardown_method(self) -> None:
        """每个测试后清理"""
        clear_trace_hooks()
        if "MINIAGENT_TRACE_LOG_FILE" in os.environ:
            del os.environ["MINIAGENT_TRACE_LOG_FILE"]

    def test_auto_register_creates_file(self) -> None:
        """测试自动注册创建日志文件"""
        tmpfile = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
        tmpfile.close()

        os.environ["MINIAGENT_TRACE_LOG_FILE"] = tmpfile.name
        auto_register_trace_file_hook()

        emit_trace({"type": "file_test", "data": "persisted"})

        # 验证文件内容
        with open(tmpfile.name, "r", encoding="utf-8") as f:
            content = f.read()
            assert "file_test" in content
            assert "ts" in content  # 时间戳自动添加

        Path(tmpfile.name).unlink()

    def test_auto_register_skips_without_env(self) -> None:
        """测试无环境变量时不注册"""
        auto_register_trace_file_hook()
        # 没有钩子被注册
        events: list[dict] = []
        register_trace_hook(lambda e: events.append(e))
        emit_trace({"type": "test"})
        # 只有手动注册的钩子被调用
        assert len(events) == 1

    def test_clear_hooks_reset_file_config(self) -> None:
        """测试 clear_trace_hooks 同时清除文件配置"""
        tmpfile = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
        tmpfile.close()

        os.environ["MINIAGENT_TRACE_LOG_FILE"] = tmpfile.name
        auto_register_trace_file_hook()

        # 清空后，文件配置也应被清除
        clear_trace_hooks()

        # 清除环境变量，防止 auto_register 重新注册
        del os.environ["MINIAGENT_TRACE_LOG_FILE"]

        # 再次调用 auto_register（无环境变量，不会注册）
        auto_register_trace_file_hook()

        # 没有事件被写入（因为 clear 已重置且无环境变量）
        emit_trace({"type": "after_clear"})
        with open(tmpfile.name, "r", encoding="utf-8") as f:
            content = f.read()
            # 文件应该是空的（clear 后没有写入）
            assert "after_clear" not in content

        Path(tmpfile.name).unlink()

    def test_creates_directory_if_missing(self) -> None:
        """测试自动创建目录"""
        tmpdir = tempfile.mkdtemp()
        log_path = os.path.join(tmpdir, "subdir", "trace.jsonl")

        os.environ["MINIAGENT_TRACE_LOG_FILE"] = log_path
        auto_register_trace_file_hook()

        emit_trace({"type": "dir_test"})

        # 目录应该被创建
        assert os.path.isdir(os.path.dirname(log_path))
        # 文件应该存在
        assert os.path.isfile(log_path)

        Path(log_path).unlink()
        os.rmdir(os.path.dirname(log_path))
        os.rmdir(tmpdir)