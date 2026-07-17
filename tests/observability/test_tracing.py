"""Tests for miniagent.agent.observability

验证 trace 钩子系统，包括可选的持久化功能。
"""

import builtins
import os
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from miniagent.agent import observability as tracing
from miniagent.agent.observability import (
    AsyncTraceWriter,
    TraceRuntimeConfig,
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
from tests.support.config import install_test_config


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

    def test_explicit_runtime_config_starts_outside_agent_context(
        self, tmp_path: Path
    ) -> None:
        config = TraceRuntimeConfig(enabled=True, output_dir=str(tmp_path / "explicit"))

        auto_register_trace_file_hook(config)
        emit_trace({"type": "explicit_config"})
        actual_path = get_actual_trace_file()
        stats = shutdown_trace_writer()

        assert actual_path is not None and actual_path.is_file()
        assert stats is not None
        assert stats["emitted_count"] == stats["written_count"] == 1
        assert "explicit_config" in actual_path.read_text(encoding="utf-8")

    def test_failed_start_rolls_back_and_allows_retry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        original_start = AsyncTraceWriter.start

        def fail_start(self, file_path):
            raise OSError("read only")

        monkeypatch.setattr(AsyncTraceWriter, "start", fail_start)
        with pytest.raises(OSError, match="read only"):
            auto_register_trace_file_hook(
                TraceRuntimeConfig(enabled=True, output_dir=str(tmp_path / "failed"))
            )
        assert tracing._auto_initialized is False
        assert tracing._trace_writer is None

        monkeypatch.setattr(AsyncTraceWriter, "start", original_start)
        auto_register_trace_file_hook(
            TraceRuntimeConfig(enabled=True, output_dir=str(tmp_path / "retry"))
        )
        assert get_actual_trace_file() is not None
        shutdown_trace_writer()

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


def test_json_shape_and_usage_sanitization_edges() -> None:
    count, truncated = tracing._json_shape_char_count(
        {
            "text": "abc",
            "bytes": b"xy",
            "list": [None, True, False, 12, 1.5, object()],
        }
    )
    assert count > 20
    assert truncated is False
    assert tracing._json_shape_char_count([1, 2, 3], max_nodes=1)[1] is True

    usage = tracing._sanitize_usage_metrics(
        {
            1: 10,
            "input_tokens": 20,
            "output_tokens": True,
            "ignored": 99,
            "input_tokens_details": {
                "cached_tokens": 4,
                "bad": True,
                2: 3,
            },
            "output_tokens_details": {"bad": "value"},
        }
    )
    assert usage == {
        "input_tokens": 20,
        "input_tokens_details": {"cached_tokens": 4},
    }
    assert tracing.new_trace_id("!@#") .startswith("trace-")
    assert tracing.new_trace_id("a" * 30).startswith("a" * 16 + "-")


def test_trace_span_reports_success_failure_and_parent() -> None:
    events: list[dict] = []
    clear_trace_hooks()
    register_trace_hook(events.append)
    with tracing.trace_parent("parent"):
        with tracing.trace_span("plan", session_key="session", span_id="span") as span:
            assert span == "span"
    assert events[-1]["success"] is True
    assert events[-1]["parent_span_id"] == "parent"

    with pytest.raises(RuntimeError):
        with tracing.trace_span("execute", parent_span_id="explicit"):
            raise RuntimeError("failed")
    assert events[-1]["success"] is False
    assert events[-1]["parent_span_id"] == "explicit"
    clear_trace_hooks()


def test_resource_sampler_collects_optional_metrics_and_errors(monkeypatch) -> None:
    sampler = tracing.TraceResourceSampler(0.001)
    sampler._process = SimpleNamespace(
        memory_info=lambda: SimpleNamespace(rss=123),
        cpu_times=lambda: SimpleNamespace(user=1.5, system=0.5),
    )
    sampler._tracemalloc = SimpleNamespace(get_traced_memory=lambda: (10, 20), stop=lambda: None)
    sampler._owns_tracemalloc = True
    event = sampler._sample()
    assert event["rss_bytes"] == 123
    assert event["cpu_user_ms"] == 1500
    assert event["python_traced_peak_bytes"] == 20

    sampler._process = SimpleNamespace(memory_info=MagicMock(side_effect=OSError("gone")))
    sampler._tracemalloc = SimpleNamespace(
        get_traced_memory=MagicMock(side_effect=RuntimeError("disabled")),
        stop=MagicMock(side_effect=RuntimeError("disabled")),
    )
    assert "rss_bytes" not in sampler._sample()
    sampler.shutdown()
    assert sampler._owns_tracemalloc is False

    events: list[dict] = []
    monkeypatch.setattr(tracing, "emit_trace", events.append)
    sampler = tracing.TraceResourceSampler(0.05)
    sampler.start()
    sampler.start()
    sampler.shutdown()
    assert events


def test_resource_sampler_does_not_enable_tracemalloc_by_default() -> None:
    sampler = tracing.TraceResourceSampler(0.05)
    try:
        assert sampler._tracemalloc is None
        event = sampler._sample()
        assert "python_traced_bytes" not in event
    finally:
        sampler.shutdown()


def test_resource_sampler_tolerates_unavailable_tracemalloc(monkeypatch) -> None:
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "tracemalloc":
            raise ImportError("disabled")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    sampler = tracing.TraceResourceSampler(0.05, track_python_allocations=True)
    try:
        assert sampler._tracemalloc is None
    finally:
        sampler.shutdown()


def test_writer_rejects_events_and_maintenance_after_terminal_states() -> None:
    writer = AsyncTraceWriter(queue_max_size=1)
    writer._shutdown = True
    writer.emit({"type": "ignored"})
    assert writer.stats()["emitted_count"] == 0

    writer._shutdown = False
    writer._queue.put_nowait({"type": "occupies-queue"})
    assert writer.exclude_session("session", timeout=0.001) == 0


def test_writer_shutdown_timeout_leaves_background_handle_open() -> None:
    writer = AsyncTraceWriter()
    thread = MagicMock()
    thread.is_alive.return_value = True
    writer._writer_thread = thread

    assert writer.shutdown(timeout_seconds=0.001) is False
    assert writer.stats()["shutdown_incomplete"] is True
    thread.join.assert_called_once()


def test_default_trace_file_uses_scoped_agent_settings(tmp_path: Path) -> None:
    from miniagent.agent.settings import AgentSettings, use_agent_settings

    with use_agent_settings(AgentSettings({"trace": {"output_dir": str(tmp_path)}})):
        path = tracing._get_default_trace_file()

    assert path.parent == tmp_path


def test_sampler_start_failure_rolls_back_writer_and_sampler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shutdowns: list[bool] = []

    def fail_start(_self) -> None:
        raise RuntimeError("sampler failed")

    monkeypatch.setattr(tracing.TraceResourceSampler, "start", fail_start)
    monkeypatch.setattr(
        tracing.TraceResourceSampler,
        "shutdown",
        lambda _self: shutdowns.append(True),
    )

    with pytest.raises(RuntimeError, match="sampler failed"):
        auto_register_trace_file_hook(
            TraceRuntimeConfig(
                enabled=True,
                output_dir=str(tmp_path),
                resource_sample_interval_seconds=0.1,
            )
        )

    assert shutdowns == [True]
    assert tracing._trace_writer is None
    assert tracing._resource_sampler is None


def test_incomplete_global_shutdown_keeps_writer_for_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clear_trace_hooks()
    auto_register_trace_file_hook(
        TraceRuntimeConfig(enabled=True, output_dir=str(tmp_path / "trace"))
    )
    writer = tracing._trace_writer
    assert writer is not None
    original_shutdown = writer.shutdown

    def incomplete(timeout_seconds=5.0):
        writer._shutdown_incomplete = True
        return False

    monkeypatch.setattr(writer, "shutdown", incomplete)
    stats = shutdown_trace_writer(timeout_seconds=0.01)
    assert stats is not None and stats["shutdown_incomplete"] is True
    assert tracing._trace_writer is writer
    clear_trace_hooks()
    assert tracing._trace_writer is writer
    assert tracing._auto_initialized is True

    monkeypatch.setattr(writer, "shutdown", original_shutdown)
    writer._shutdown_incomplete = False
    shutdown_trace_writer()
    assert tracing._trace_writer is None


def test_persistence_sanitizer_allows_only_declared_shapes() -> None:
    sanitized = tracing._sanitize_trace_event_for_persistence(
        {
            "type": "test",
            "error_preview": "secret preview",
            "duration_ms": 3,
            "status": "ok",
            "retry_adjustments": ["a", "b"],
            "output_item_types": ["x", 2],
            "content": "secret",
            "unknown": ["secret"],
            "usage": {"prompt_tokens": 4, "debug": "secret"},
        }
    )
    assert sanitized["error_preview_chars"] == len("secret preview")
    assert sanitized["duration_ms"] == 3
    assert sanitized["status"] == "ok"
    assert sanitized["retry_adjustments"] == ["a", "b"]
    assert sanitized["usage"] == {"prompt_tokens": 4}
    assert "content" not in sanitized
    assert "unknown" not in sanitized
    assert "output_item_types" not in sanitized


def test_global_trace_helpers_without_writer_and_duplicate_hooks() -> None:
    clear_trace_hooks()
    hook = MagicMock()
    register_trace_hook(hook)
    register_trace_hook(hook)
    emit_trace({"type": "once"})
    hook.assert_called_once()
    unregister_trace_hook(hook)
    unregister_trace_hook(hook)
    assert tracing.get_trace_writer_stats() is None
    assert tracing.exclude_trace_session("s") == (None, 0)
    assert tracing.finalize_trace_session("s") == (None, 0)
    assert shutdown_trace_writer() is None
    clear_trace_hooks()
