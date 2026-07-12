"""Tests for miniagent.infrastructure.tracing

验证 trace 钩子系统，包括可选的持久化功能。
"""

import os
import time
from pathlib import Path

from miniagent.infrastructure.tracing import (
    AsyncTraceWriter,
    auto_register_trace_file_hook,
    clear_trace_hooks,
    emit_trace,
    get_actual_trace_file,
    get_trace_writer_stats,
    register_trace_hook,
    shutdown_trace_writer,
    trace_parent,
    unregister_trace_hook,
)
from tests.config_helpers import install_test_config


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

    def test_events_inherit_current_span_and_session(self) -> None:
        events: list[dict] = []
        register_trace_hook(events.append)

        with trace_parent("agent-span", session_key="session-1"):
            emit_trace({"type": "embedding.api_call", "purpose": "query"})
            emit_trace(
                {
                    "type": "explicit",
                    "parent_span_id": "explicit-parent",
                    "session_key": "explicit-session",
                }
            )

        assert events[0]["parent_span_id"] == "agent-span"
        assert events[0]["session_key"] == "session-1"
        assert events[1]["parent_span_id"] == "explicit-parent"
        assert events[1]["session_key"] == "explicit-session"

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
        """每个测试前清空钩子"""
        clear_trace_hooks()

    def teardown_method(self) -> None:
        """每个测试后清理"""
        clear_trace_hooks()

    def test_auto_register_creates_file(self, tmp_path: Path) -> None:
        """测试自动注册创建日志文件"""
        log_path = tmp_path / "trace.jsonl"
        install_test_config(tmp_path, {"debug": {"log_path": str(log_path)}})
        auto_register_trace_file_hook()

        emit_trace({"type": "file_test", "data": "persisted"})

        # 关闭异步写入器，确保事件已写入（优雅等待）
        shutdown_trace_writer()

        # 进程隔离优化：文件名添加pid后缀
        pid_suffix = f"-pid{os.getpid()}"
        expected_file = Path(str(log_path).replace(".jsonl", f"{pid_suffix}.jsonl"))

        # 若 writer 尚未产生 pid 分片，则使用测试配置路径定位输出。
        actual_file = expected_file if expected_file.exists() else log_path

        # 验证文件内容
        with open(actual_file, encoding="utf-8") as f:
            content = f.read()
            assert "file_test" in content
            assert "ts" in content  # 时间戳自动添加

        log_path.unlink(missing_ok=True)
        expected_file.unlink(missing_ok=True)

    def test_auto_register_skips_without_config(self, tmp_path: Path) -> None:
        """测试无配置时不注册文件持久化"""
        install_test_config(tmp_path, {})
        auto_register_trace_file_hook()
        # 没有钩子被注册
        events: list[dict] = []
        register_trace_hook(lambda e: events.append(e))
        emit_trace({"type": "test"})
        # 只有手动注册的钩子被调用
        assert len(events) == 1

    def test_clear_hooks_reset_file_config(self, tmp_path: Path) -> None:
        """测试 clear_trace_hooks 同时清除文件配置"""
        log_path = tmp_path / "trace.jsonl"
        install_test_config(tmp_path, {"debug": {"log_path": str(log_path)}})
        auto_register_trace_file_hook()

        # 清空后，文件配置也应被清除
        clear_trace_hooks()

        # 再次调用 auto_register（无配置，不会注册）
        install_test_config(tmp_path, {})
        auto_register_trace_file_hook()

        # 没有事件被写入（因为 clear 已重置且无配置）
        emit_trace({"type": "after_clear"})
        if log_path.exists():
            content = log_path.read_text(encoding="utf-8")
            assert "after_clear" not in content

    def test_creates_directory_if_missing(self, tmp_path: Path) -> None:
        """测试自动创建目录"""
        log_path = tmp_path / "subdir" / "trace.jsonl"
        install_test_config(tmp_path, {"debug": {"log_path": str(log_path)}})
        auto_register_trace_file_hook()

        emit_trace({"type": "dir_test"})

        # 关闭异步写入器（释放文件句柄）
        shutdown_trace_writer()

        # 目录应该被创建
        assert log_path.parent.is_dir()

        # 进程隔离优化：文件名添加pid后缀
        pid_suffix = f"-pid{os.getpid()}"
        expected_file = Path(str(log_path).replace(".jsonl", f"{pid_suffix}.jsonl"))

        # 文件应该存在（考虑pid后缀）
        assert expected_file.is_file() or log_path.is_file()

        expected_file.unlink(missing_ok=True)
        log_path.unlink(missing_ok=True)

    def test_actual_trace_file_and_writer_stats(self, tmp_path: Path) -> None:
        """持久化启动后可查询实际 pid 分片路径和 writer 指标。"""
        log_path = tmp_path / "trace.jsonl"
        install_test_config(tmp_path, {"debug": {"log_path": str(log_path)}})
        auto_register_trace_file_hook()

        actual_path = get_actual_trace_file()
        assert actual_path is not None
        assert f"-pid{os.getpid()}" in actual_path.name

        emit_trace({"type": "stats_test"})
        stats = get_trace_writer_stats()
        assert stats is not None
        assert stats["file_path"] == str(actual_path)
        assert stats["emitted_count"] >= 1

        shutdown_trace_writer()
        actual_path.unlink(missing_ok=True)

    def test_async_writer_bounded_queue_drops_without_blocking(self, tmp_path: Path) -> None:
        """队列满时 writer 必须非阻塞丢弃事件，防止 trace 高峰撑爆内存。"""
        log_path = tmp_path / "trace.jsonl"
        writer = AsyncTraceWriter(
            batch_interval=10.0,
            batch_size=1000,
            queue_max_size=2,
            overflow_policy="drop_oldest",
        )
        writer.start(log_path)

        start = time.perf_counter()
        for i in range(50):
            writer.emit({"type": "overflow_test", "index": i})
        elapsed = time.perf_counter() - start
        stats = writer.stats()

        writer.shutdown()
        assert elapsed < 0.1
        assert stats["dropped_count"] > 0
        assert stats["queue_max_size"] == 2
        assert writer.file_path is not None
        writer.file_path.unlink(missing_ok=True)
        log_path.unlink(missing_ok=True)

    def test_metrics_only_persistence_drops_payload_fields(self, tmp_path: Path) -> None:
        """metrics_only 持久化不应落 prompt/response/tool args 等正文负载。"""
        log_path = tmp_path / "trace.jsonl"
        install_test_config(
            tmp_path,
            {
                "debug": {"log_path": str(log_path)},
                "trace": {"record_payload": "metrics_only"},
            },
        )
        auto_register_trace_file_hook()

        emit_trace(
            {
                "type": "llm.response",
                "model": "test-model",
                "content": "SECRET_RESPONSE_BODY",
                "messages": [{"role": "user", "content": "SECRET_PROMPT"}],
                "args_truncated": "SECRET_ARGS",
                "usage": {"prompt_tokens": 1, "completion_tokens": 2},
                "duration_ms": 123,
            }
        )
        actual_path = get_actual_trace_file()
        shutdown_trace_writer()

        assert actual_path is not None
        content = actual_path.read_text(encoding="utf-8")
        assert "SECRET_RESPONSE_BODY" not in content
        assert "SECRET_PROMPT" not in content
        assert "SECRET_ARGS" not in content
        assert "duration_ms" in content
        assert "prompt_tokens" in content
        actual_path.unlink(missing_ok=True)

    def test_metrics_only_persistence_keeps_safe_responses_usage_details(
        self,
        tmp_path: Path,
    ) -> None:
        """Responses usage detail counters remain available to daily reports."""
        log_path = tmp_path / "trace.jsonl"
        install_test_config(
            tmp_path,
            {
                "debug": {"log_path": str(log_path)},
                "trace": {"record_payload": "metrics_only"},
            },
        )
        auto_register_trace_file_hook()
        emit_trace(
            {
                "type": "llm.response",
                "usage": {
                    "input_tokens": 20,
                    "output_tokens": 7,
                    "input_tokens_details": {"cached_tokens": 4},
                    "output_tokens_details": {"reasoning_tokens": 3},
                    "provider_debug": "MUST_NOT_PERSIST",
                },
            }
        )
        actual_path = get_actual_trace_file()
        shutdown_trace_writer()

        assert actual_path is not None
        content = actual_path.read_text(encoding="utf-8")
        assert '"input_tokens":20' in content
        assert '"cached_tokens":4' in content
        assert '"reasoning_tokens":3' in content
        assert "MUST_NOT_PERSIST" not in content

    def test_metrics_only_drops_unknown_string_fields(self, tmp_path: Path) -> None:
        log_path = tmp_path / "trace.jsonl"
        install_test_config(
            tmp_path,
            {
                "debug": {"log_path": str(log_path)},
                "trace": {"record_payload": "metrics_only"},
            },
        )
        auto_register_trace_file_hook()
        emit_trace(
            {
                "type": "custom.metric",
                "unknown_secret": "MUST_NEVER_PERSIST",
                "unknown_numeric_identifier": 123456789,
                "duration_ms": 7,
            }
        )
        actual_path = get_actual_trace_file()
        shutdown_trace_writer()

        assert actual_path is not None
        content = actual_path.read_text(encoding="utf-8")
        assert "MUST_NEVER_PERSIST" not in content
        assert "123456789" not in content
        assert '"duration_ms":7' in content
        actual_path.unlink(missing_ok=True)
