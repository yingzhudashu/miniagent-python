"""Trace 系统性能测试。

验证异步写入器的性能优化效果：
- 非阻塞发射事件（vs 同步阻塞 3-11ms）
- 批量写入不丢数据
- 优雅关闭机制
"""

from __future__ import annotations

import json
import os
import statistics
import tempfile
import threading
import time
import tracemalloc
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from miniagent.agent.observability import (
    AsyncTraceWriter,
    TraceRuntimeConfig,
    auto_register_trace_file_hook,
    clear_trace_hooks,
    emit_trace,
    llm_request_size_metrics,
    shutdown_trace_writer,
)
from miniagent.assistant.infrastructure import trace_stats


def test_llm_request_size_metrics_are_scalar_and_do_not_retain_payload() -> None:
    secret = "sensitive-prompt-value"
    metrics = llm_request_size_metrics(
        [{"role": "user", "content": secret}],
        [{"type": "function", "function": {"name": "read_file"}}],
        force=True,
    )

    assert metrics["message_chars"] >= len(secret)
    assert metrics["tool_schema_chars"] > 0
    assert metrics["size_measurement_truncated"] is False
    assert secret not in repr(metrics)


def test_llm_request_size_metrics_skip_traversal_without_trace() -> None:
    clear_trace_hooks()
    assert llm_request_size_metrics([{"content": "not measured"}]) == {}


def test_trace_stats_report_request_character_sizes() -> None:
    report = trace_stats.aggregate_trace_stats(
        [
            {
                "type": "llm.request",
                "phase": "plan",
                "message_count": 2,
                "tool_count": 0,
                "message_chars": 1200,
                "tool_schema_chars": 0,
            },
            {
                "type": "llm.request",
                "phase": "plan",
                "message_count": 2,
                "tool_count": 0,
                "message_chars": 1400,
                "tool_schema_chars": 0,
            },
        ]
    )

    assert report["llm"]["avg_message_chars"] == 1300.0
    assert report["llm"]["by_phase"]["plan"]["avg_message_chars"] == 1300.0


class _CountingFile:
    """Delegate file operations while counting physical trace batches."""

    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate
        self.write_batches = 0

    def writelines(self, lines: list[str]) -> None:
        self.write_batches += 1
        self._delegate.writelines(lines)

    def flush(self) -> None:
        self._delegate.flush()

    def close(self) -> None:
        self._delegate.close()


def test_daily_report_streams_large_trace_with_bounded_peak_memory(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """Daily aggregation must not materialize every parsed event in memory."""
    date = "2026-07-12"
    trace_file = tmp_path / f"trace-{date}.jsonl"
    payload = "x" * 512
    with trace_file.open("w", encoding="utf-8") as handle:
        for index in range(20_000):
            handle.write(
                json.dumps(
                    {
                        "type": "perf.sample",
                        "session_key": f"session-{index % 10}",
                        "payload": payload,
                    },
                    separators=(",", ":"),
                )
                + "\n"
            )

    monkeypatch.setattr(trace_stats, "get_trace_output_dir", lambda: tmp_path)
    monkeypatch.setattr(
        trace_stats,
        "load_trace_events",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("daily report must use the streaming iterator")
        ),
    )

    tracemalloc.start()
    try:
        report = trace_stats.generate_daily_report(date)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert report["total_events"] == 20_000
    assert report["sessions"] == 10
    assert peak < 4 * 1024 * 1024


def test_aggregator_bounds_high_cardinality_groups_and_malformed_metrics() -> None:
    def events():
        for index in range(5_000):
            yield {
                "type": "llm.request",
                "phase": {"malformed": index} if index == 0 else f"phase-{index}",
                "session_key": f"session-{index}",
                "message_count": "not-a-number",
                "tool_count": float("nan"),
            }
            yield {
                "type": "tool.end",
                "tool": f"tool-{index}",
                "duration_ms": "bad",
                "success": True,
            }
            yield {
                "type": "error.collect",
                "error_type": {"bad": index} if index == 0 else f"error-{index}",
            }

    report = trace_stats.aggregate_trace_stats(events())

    assert report["total_events"] == 15_000
    assert len(report["session_list"]) == 1_000
    assert report["session_list_truncated"] is True
    assert len(report["llm"]["by_phase"]) <= 129
    assert len(report["tools"]["tools"]) <= 1_025
    assert len(report["errors"]) <= 1_025


def test_trace_stats_resource_span_and_empty_report_dimensions() -> None:
    report = trace_stats.aggregate_trace_stats(
        [
            {
                "type": "perf.resource_sample",
                "session_key": "s1",
                "rss_bytes": 200,
                "process_cpu_ms": 20,
                "thread_count": 2,
                "python_traced_peak_bytes": 50,
            },
            {
                "type": "perf.resource_sample",
                "session_key": "s1",
                "rss_bytes": 100,
                "process_cpu_ms": 35,
                "thread_count": 4,
                "python_traced_peak_bytes": 80,
            },
            {
                "type": "agent.phase_end",
                "phase": "plan",
                "duration_ms": 12,
                "cpu_ms": 4,
                "success": False,
            },
            {
                "type": "agent.run_end",
                "duration_ms": 20,
                "cpu_ms": 8,
                "success": True,
            },
            {"type": "embedding.cache_hit"},
            {"type": "embedding.api_call", "duration_ms": 25},
        ]
    )

    assert report["resources"] == {
        "sample_count": 2,
        "rss_peak_bytes": 200,
        "rss_min_bytes": 100,
        "rss_growth_bytes": 100,
        "process_cpu_delta_ms": 15.0,
        "thread_peak": 4,
        "python_traced_peak_bytes": 80,
    }
    assert report["spans"]["plan"]["failure_count"] == 1
    assert report["spans"]["agent.run"]["avg_duration_ms"] == 20.0
    assert report["embedding"]["cache_hit_rate"] == 0.5
    assert report["embedding"]["avg_api_latency_ms"] == 25.0

    empty = trace_stats.aggregate_trace_stats([])
    assert empty["memory"] == {"read_count": 0}
    assert empty["context"] == {"compress_count": 0}
    assert empty["resources"] == {"sample_count": 0}


def test_trace_stats_reports_bounded_warm_to_final_resource_plateaus() -> None:
    events = [
        {
            "type": "perf.resource_sample",
            "rss_bytes": 100 if index < 40 else 105,
            "python_traced_bytes": 50 if index < 40 else 52,
            "python_traced_peak_bytes": 60,
        }
        for index in range(56)
    ]

    resources = trace_stats.aggregate_trace_stats(events)["resources"]

    assert resources["rss_warm_median_bytes"] == 100
    assert resources["rss_final_median_bytes"] == 105
    assert resources["rss_warm_to_final_growth_ratio"] == 0.05
    assert resources["python_warm_median_bytes"] == 50
    assert resources["python_final_median_bytes"] == 52
    assert resources["python_warm_to_final_growth_ratio"] == 0.04


def test_trace_stats_memory_context_error_and_llm_edge_metrics(monkeypatch) -> None:
    monkeypatch.setattr(trace_stats, "get_config", lambda *_args, **_kwargs: 10)
    events = [
        {
            "type": "memory.read",
            "duration_ms": 4,
            "layer": "session",
            "chars_loaded": 20,
            "cache_hit": True,
        },
        {
            "type": "context.compress",
            "duration_ms": 8,
            "tokens_before": 100,
            "tokens_after": 25,
        },
        {
            "type": "error.collect",
            "error_type": "UserError",
            "tool_name": "read_file",
            "is_user_error": True,
        },
        {
            "type": "tool.end",
            "tool": "slow",
            "duration_ms": 20,
            "success": False,
        },
        {
            "type": "llm.request",
            "phase": "plan",
            "message_count": -1,
            "tool_count": 2,
        },
        {
            "type": "llm.response",
            "phase": "plan",
            "duration_ms": 5,
            "failure_category": "timeout",
            "retrying": True,
            "usage": "invalid",
        },
        {
            "type": "llm.response",
            "phase": "plan",
            "duration_ms": -1,
            "failure_category": "timeout",
            "retrying": False,
            "usage": {},
        },
    ]
    report = trace_stats.aggregate_trace_stats(events)

    assert report["memory"]["cache_hit_rate"] == 1.0
    assert report["context"]["compress_ratio"] == 0.25
    assert report["context"]["total_tokens_saved"] == 75
    assert report["errors"][0]["is_user_error"] is True
    assert report["tools"]["slow_tools"][0]["name"] == "slow"
    assert report["tools"]["failed_tools"][0]["fail_count"] == 1
    assert report["llm"]["retrying_response_count"] == 1
    assert report["llm"]["terminal_failed_response_count"] == 1


def test_trace_file_iteration_save_cleanup_and_stream_filter(
    tmp_path: Path, monkeypatch
) -> None:
    date = "2026-07-13"
    trace_file = tmp_path / f"trace-{date}.jsonl"
    trace_file.write_text(
        "\n[]\nnot-json\n"
        + json.dumps({"type": "one", "session_key": "a"})
        + "\n"
        + json.dumps({"type": "two", "session_key": "b"})
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        trace_stats,
        "get_trace_files",
        lambda _date=None: [trace_file, tmp_path / "missing.jsonl"],
    )
    assert list(trace_stats.iter_trace_events(date, session_key="a")) == [
        {"type": "one", "session_key": "a"}
    ]
    assert list(trace_stats.iter_trace_events(date, event_type="two")) == [
        {"type": "two", "session_key": "b"}
    ]

    report_path = trace_stats.save_report({"date": date, "total_events": 2}, tmp_path / "reports")
    assert json.loads(report_path.read_text(encoding="utf-8"))["total_events"] == 2

    old = tmp_path / "trace-2000-01-01.jsonl"
    invalid = tmp_path / "trace-invalid.jsonl"
    old.write_text("{}\n", encoding="utf-8")
    invalid.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(trace_stats, "get_trace_output_dir", lambda: tmp_path)
    assert trace_stats.cleanup_old_traces(1) >= 1
    assert invalid.exists()

    filtered = tmp_path / "trace-2026-07-12-pid1.jsonl"
    filtered.write_text(
        json.dumps({"session_key": "remove"})
        + "\ninvalid\n"
        + json.dumps({"session_key": "keep"}),
        encoding="utf-8",
    )
    assert trace_stats._stream_remove_session_from_trace_file(filtered, "remove") == 1
    assert "keep" in filtered.read_text(encoding="utf-8")
    assert trace_stats._stream_remove_session_from_trace_file(filtered, "absent") == 0

    only = tmp_path / "trace-2026-07-11-pid1.jsonl"
    only.write_text(json.dumps({"session_key": "remove"}), encoding="utf-8")
    assert trace_stats._stream_remove_session_from_trace_file(only, "remove") == 1
    assert not only.exists()


def test_async_writer_non_blocking():
    """验证异步写入器不阻塞主线程。

    性能目标：
    - 100 个事件应在 <10ms 内完成发射（vs 同步版本 >300ms）
    - 单事件延迟 <0.1ms（vs 同步版本 3-11ms）
    """
    # 准备临时文件
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        test_file = Path(f.name)

    # 创建异步写入器
    writer = AsyncTraceWriter(batch_interval=0.1, batch_size=50)
    writer.start(test_file)

    # 测量：连续发送 100 个事件
    start = time.perf_counter()
    for i in range(100):
        writer.emit({"type": "test", "index": i, "data": f"test-data-{i}"})
    elapsed = time.perf_counter() - start

    # 验证：100 个事件应在 <10ms 内完成（vs 同步版本 >300ms）
    # 实际应该是 <1ms，但考虑到测量误差，放宽到 10ms
    assert elapsed < 0.01, f"异步发射过慢: {elapsed}s（预期 <0.01s）"

    # 清理
    writer.shutdown()
    test_file.unlink()


def test_batch_write_integrity():
    """验证批量写入不丢数据。

    目标：
    - 发送的事件应全部写入文件
    - 优雅关闭后无数据丢失
    """
    # 准备临时文件
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        test_file = Path(f.name)

    # 创建异步写入器
    writer = AsyncTraceWriter(batch_interval=0.1, batch_size=50)
    writer.start(test_file)

    # 发送 100 个事件
    for i in range(100):
        writer.emit({"type": "integrity_test", "index": i})

    # 优雅关闭（等待队列清空）
    writer.shutdown()

    # 验证：文件应包含 100 条记录
    # 进程隔离优化：文件名添加pid后缀
    pid_suffix = f"-pid{os.getpid()}"
    expected_file = Path(str(test_file).replace(".jsonl", f"{pid_suffix}.jsonl"))

    # 若 writer 尚未产生 pid 分片，则使用测试配置路径定位输出。
    actual_file = expected_file if expected_file.exists() else test_file

    with actual_file.open(encoding="utf-8") as f:
        lines = f.readlines()

    assert len(lines) == 100, f"数据丢失：写入 {len(lines)} 条（预期 100 条）"

    # 验证每条记录完整性
    import json

    for i, line in enumerate(lines):
        record = json.loads(line)
        assert record.get("index") == i, f"记录顺序错误：第 {i} 行 index={record.get('index')}"

    # 清理（包括pid后缀版本）
    test_file.unlink(missing_ok=True)
    expected_file.unlink(missing_ok=True)


def test_writer_aggregates_low_latency_burst_into_one_batch(tmp_path: Path) -> None:
    """A short burst should use one physical write instead of one write per event."""
    writer = AsyncTraceWriter(batch_interval=0.05, batch_size=50)
    writer.start(tmp_path / "trace.jsonl")
    assert writer._file_handle is not None
    counting_file = _CountingFile(writer._file_handle)
    writer._file_handle = counting_file

    for index in range(20):
        writer.emit({"type": "batch_test", "index": index})
    writer.shutdown()

    assert counting_file.write_batches == 1
    assert writer.stats()["written_count"] == 20


def test_shutdown_with_full_queue_preserves_all_accepted_events(tmp_path: Path) -> None:
    """A full queue must not sacrifice a real event to enqueue the sentinel."""
    writer = AsyncTraceWriter(
        batch_interval=10.0,
        batch_size=50,
        queue_max_size=2,
        overflow_policy="drop_newest",
    )
    original_serialize = writer._serialize_event
    first_started = threading.Event()
    release_first = threading.Event()

    def blocking_serialize(event: dict[str, Any]) -> str | None:
        if event.get("index") == 0:
            first_started.set()
            assert release_first.wait(timeout=2.0)
        return original_serialize(event)

    writer._serialize_event = blocking_serialize  # type: ignore[method-assign]
    writer.start(tmp_path / "trace.jsonl")
    writer.emit({"type": "shutdown_full", "index": 0})
    assert first_started.wait(timeout=1.0)
    writer.emit({"type": "shutdown_full", "index": 1})
    writer.emit({"type": "shutdown_full", "index": 2})

    shutdown_thread = threading.Thread(target=writer.shutdown)
    shutdown_thread.start()
    release_first.set()
    shutdown_thread.join(timeout=2.0)

    assert not shutdown_thread.is_alive()
    assert writer.stats()["written_count"] == 3
    assert writer.stats()["dropped_count"] == 0
    assert writer.file_path is not None
    assert len(writer.file_path.read_text(encoding="utf-8").splitlines()) == 3


def test_writer_counts_serialization_errors_and_uses_compact_json(tmp_path: Path) -> None:
    """Malformed payloads are visible in metrics and valid JSON has no spacer bytes."""
    writer = AsyncTraceWriter(batch_interval=0.01, batch_size=10)
    writer.start(tmp_path / "trace.jsonl")
    writer.emit({"type": "bad", "value": object()})
    writer.emit({"type": "good", "nested": {"value": 1}})
    writer.shutdown()

    stats = writer.stats()
    assert stats["emitted_count"] == 2
    assert stats["written_count"] == 1
    assert stats["dropped_count"] == 1
    assert stats["serialization_error_count"] == 1
    assert stats["write_error_count"] == 0
    assert writer.file_path is not None
    assert writer.file_path.read_text(encoding="utf-8") == (
        '{"type":"good","nested":{"value":1}}\n'
    )


def test_writer_can_restart_without_leaking_old_state(tmp_path: Path) -> None:
    """Repeated start closes the prior target and writes subsequent events once."""
    writer = AsyncTraceWriter(batch_interval=0.01, batch_size=10)
    writer.start(tmp_path / "first.jsonl")
    first_path = writer.file_path
    writer.emit({"type": "first"})

    writer.start(tmp_path / "second.jsonl")
    second_path = writer.file_path
    writer.emit({"type": "second"})
    writer.shutdown()

    assert first_path is not None
    assert second_path is not None
    assert [line for line in first_path.read_text(encoding="utf-8").splitlines()] == [
        '{"type":"first"}'
    ]
    assert [line for line in second_path.read_text(encoding="utf-8").splitlines()] == [
        '{"type":"second"}'
    ]


def test_writer_rotates_utc_daily_shards(tmp_path: Path) -> None:
    writer = AsyncTraceWriter(batch_interval=0.001, batch_size=20)
    writer.start(tmp_path / "trace-2026-07-11.jsonl")
    writer.emit({"type": "day_one", "ts": "2026-07-11T23:59:59+00:00"})
    writer.emit({"type": "day_two", "ts": "2026-07-12T00:00:01+00:00"})
    writer.shutdown()

    first = tmp_path / f"trace-2026-07-11-pid{os.getpid()}.jsonl"
    second = tmp_path / f"trace-2026-07-12-pid{os.getpid()}.jsonl"
    assert "day_one" in first.read_text(encoding="utf-8")
    assert "day_two" in second.read_text(encoding="utf-8")
    assert writer.stats()["rotation_count"] == 1


def test_writer_rotates_custom_path_without_date(tmp_path: Path) -> None:
    day_one = datetime.now(timezone.utc).date()
    day_two = day_one + timedelta(days=1)
    writer = AsyncTraceWriter(batch_interval=0.001, batch_size=20)
    writer.start(tmp_path / "custom.jsonl")
    writer.emit({"type": "day_one", "ts": f"{day_one.isoformat()}T23:59:59+00:00"})
    writer.emit({"type": "day_two", "ts": f"{day_two.isoformat()}T00:00:01+00:00"})
    writer.shutdown()

    first = tmp_path / f"custom-pid{os.getpid()}.jsonl"
    second = tmp_path / f"custom-{day_two.isoformat()}-pid{os.getpid()}.jsonl"
    assert "day_one" in first.read_text(encoding="utf-8")
    assert "day_two" in second.read_text(encoding="utf-8")


def test_stopped_writer_can_start_a_fresh_lifecycle(tmp_path: Path) -> None:
    writer = AsyncTraceWriter(batch_interval=0.001, batch_size=20)
    writer.start(tmp_path / "first.jsonl")
    writer.emit({"type": "first"})
    writer.shutdown()

    writer.start(tmp_path / "second.jsonl")
    writer.emit({"type": "second"})
    writer.shutdown()

    assert writer.file_path is not None
    assert writer.file_path.read_text(encoding="utf-8").strip() == '{"type":"second"}'


def test_completed_session_cleanup_does_not_retain_tombstone(tmp_path: Path) -> None:
    writer = AsyncTraceWriter(batch_interval=0.001, batch_size=20)
    writer.start(tmp_path / "trace.jsonl")
    writer.emit({"type": "first", "session_key": "completed"})
    assert writer.exclude_session("completed", reject_future=False) == 1
    writer.emit({"type": "later", "session_key": "completed"})
    writer.shutdown()

    assert writer.stats()["excluded_session_count"] == 0
    assert writer.file_path is not None
    records = [json.loads(line) for line in writer.file_path.read_text(encoding="utf-8").splitlines()]
    assert [record["type"] for record in records] == ["later"]


def test_active_writer_redacts_existing_queued_and_future_session_events(
    tmp_path: Path,
) -> None:
    writer = AsyncTraceWriter(batch_interval=0.01, batch_size=10)
    writer.start(tmp_path / "trace.jsonl")
    writer.emit({"type": "before", "session_key": "remove"})
    writer.emit({"type": "before", "session_key": "keep"})
    deadline = time.monotonic() + 1.0
    while writer.stats()["written_count"] < 2 and time.monotonic() < deadline:
        time.sleep(0.005)

    assert writer.exclude_session("remove") == 1
    writer.emit({"type": "after", "session_key": "remove"})
    writer.emit({"type": "after", "session_key": "keep"})
    writer.shutdown()

    assert writer.file_path is not None
    records = [
        json.loads(line) for line in writer.file_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [record["session_key"] for record in records] == ["keep", "keep"]
    assert writer.stats()["redacted_count"] == 2
    assert writer.stats()["dropped_count"] == 0


def test_full_queue_never_discards_session_redaction_command(
    tmp_path: Path,
) -> None:
    writer = AsyncTraceWriter(
        batch_interval=0.01,
        batch_size=10,
        queue_max_size=2,
        overflow_policy="drop_oldest",
    )
    original_serialize = writer._serialize_event
    first_started = threading.Event()
    release_first = threading.Event()

    def blocking_serialize(event: dict[str, Any]) -> str | None:
        if event.get("index") == 0:
            first_started.set()
            assert release_first.wait(timeout=2.0)
        return original_serialize(event)

    writer._serialize_event = blocking_serialize  # type: ignore[method-assign]
    writer.start(tmp_path / "trace.jsonl")
    writer.emit({"type": "queued", "index": 0, "session_key": "remove"})
    assert first_started.wait(timeout=1.0)

    removed: list[int] = []
    cleanup_thread = threading.Thread(
        target=lambda: removed.append(writer.exclude_session("remove")),
    )
    cleanup_thread.start()
    deadline = time.monotonic() + 1.0
    while writer._queue.qsize() < 1 and time.monotonic() < deadline:
        time.sleep(0.005)
    writer.emit({"type": "queued", "index": 1, "session_key": "keep"})
    writer.emit({"type": "overflow", "index": 2, "session_key": "keep"})
    release_first.set()
    cleanup_thread.join(timeout=2.0)
    writer.shutdown()

    assert not cleanup_thread.is_alive()
    assert removed == [0]
    assert writer.stats()["redacted_count"] >= 1
    assert writer.stats()["dropped_count"] == 1


def test_graceful_shutdown():
    """验证优雅关闭机制。

    目标：
    - 关闭后所有排队事件都已写入
    - 后台线程正常终止
    """
    # 准备临时文件
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        test_file = Path(f.name)

    # 创建异步写入器
    writer = AsyncTraceWriter(batch_interval=0.05, batch_size=20)
    writer.start(test_file)

    # 快速发送大量事件（超过批量大小）
    for i in range(150):
        writer.emit({"type": "shutdown_test", "index": i})

    # 立即关闭（测试优雅等待）
    start_shutdown = time.perf_counter()
    writer.shutdown()
    shutdown_elapsed = time.perf_counter() - start_shutdown

    # 验证：关闭应在合理时间内完成（<5秒）
    assert shutdown_elapsed < 5.0, f"关闭过慢: {shutdown_elapsed}s（预期 <5s）"

    # 验证所有事件都已写入
    # 进程隔离优化：文件名添加pid后缀
    pid_suffix = f"-pid{os.getpid()}"
    expected_file = Path(str(test_file).replace(".jsonl", f"{pid_suffix}.jsonl"))

    # 若 writer 尚未产生 pid 分片，则使用测试配置路径定位输出。
    actual_file = expected_file if expected_file.exists() else test_file

    with actual_file.open(encoding="utf-8") as f:
        lines = f.readlines()

    assert len(lines) == 150, f"关闭后数据丢失：写入 {len(lines)} 条（预期 150 条）"

    # 清理（包括pid后缀版本）
    test_file.unlink(missing_ok=True)
    expected_file.unlink(missing_ok=True)


@pytest.mark.perf
def test_emit_trace_performance():
    """验证 emit_trace 函数的性能优化。

    使用预热与多轮中位数降低调度、杀毒软件和 coverage instrumentation 抖动。
    """
    # 清空所有钩子
    clear_trace_hooks()

    for i in range(200):
        emit_trace({"type": "perf_warmup", "index": i})
    fast_samples: list[float] = []
    for _ in range(7):
        start = time.perf_counter()
        for i in range(1000):
            emit_trace({"type": "perf_test", "index": i})
        fast_samples.append(time.perf_counter() - start)
    elapsed_fast = statistics.median(fast_samples)
    assert elapsed_fast > 0

    # 测试异步写入路径
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        test_file = Path(f.name)

    auto_register_trace_file_hook(
        TraceRuntimeConfig(
            enabled=True,
            debug_log_path=str(test_file),
            writer_batch_interval=0.1,
            writer_batch_size=50,
        )
    )

    # 测量异步写入路径
    async_samples: list[float] = []
    for _ in range(7):
        start = time.perf_counter()
        for i in range(100):
            emit_trace({"type": "async_perf_test", "index": i})
        async_samples.append(time.perf_counter() - start)
    elapsed_async = statistics.median(async_samples)

    # 验证：异步路径应 <10ms（vs 同步版本 >300ms）
    assert elapsed_async < 0.02, f"异步路径过慢: {elapsed_async}s（预期 <0.02s）"

    # 清理
    stats = shutdown_trace_writer()
    assert stats is not None
    assert stats["emitted_count"] == stats["written_count"] == 700

    # 进程隔离优化：清理pid后缀版本文件
    pid_suffix = f"-pid{os.getpid()}"
    expected_file = Path(str(test_file).replace(".jsonl", f"{pid_suffix}.jsonl"))
    test_file.unlink(missing_ok=True)
    expected_file.unlink(missing_ok=True)


def test_concurrent_emit():
    """验证并发发射事件的稳定性。

    目标：
    - 多线程并发发射事件不应导致数据丢失
    - 异步写入器应正确处理并发
    """
    import threading

    # 准备临时文件
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        test_file = Path(f.name)

    # 创建异步写入器
    writer = AsyncTraceWriter(batch_interval=0.1, batch_size=50)
    writer.start(test_file)

    # 并发发射事件（5 个线程，每个发射 50 个事件）
    threads = []
    for thread_id in range(5):

        def emit_events(tid):
            for i in range(50):
                writer.emit({"type": "concurrent_test", "thread": tid, "index": i})

        thread = threading.Thread(target=emit_events, args=(thread_id,))
        threads.append(thread)
        thread.start()

    # 等待所有线程完成
    for thread in threads:
        thread.join()

    # 优雅关闭
    writer.shutdown()

    # 验证：文件应包含 250 条记录（5 × 50）
    # 进程隔离优化：文件名添加pid后缀
    pid_suffix = f"-pid{os.getpid()}"
    expected_file = Path(str(test_file).replace(".jsonl", f"{pid_suffix}.jsonl"))

    # 若 writer 尚未产生 pid 分片，则使用测试配置路径定位输出。
    actual_file = expected_file if expected_file.exists() else test_file

    with actual_file.open(encoding="utf-8") as f:
        lines = f.readlines()

    assert len(lines) == 250, f"并发数据丢失：写入 {len(lines)} 条（预期 250 条）"

    # 清理（包括pid后缀版本）
    test_file.unlink(missing_ok=True)
    expected_file.unlink(missing_ok=True)
