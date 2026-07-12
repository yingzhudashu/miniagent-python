"""Trace 系统性能测试。

验证异步写入器的性能优化效果：
- 非阻塞发射事件（vs 同步阻塞 3-11ms）
- 批量写入不丢数据
- 优雅关闭机制
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import tracemalloc
from pathlib import Path
from typing import Any

from miniagent.infrastructure import trace_stats
from miniagent.infrastructure.tracing import (
    AsyncTraceWriter,
    clear_trace_hooks,
    emit_trace,
)


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
            handle.write(json.dumps({
                "type": "perf.sample",
                "session_key": f"session-{index % 10}",
                "payload": payload,
            }, separators=(",", ":")) + "\n")

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


def test_async_writer_non_blocking():
    """验证异步写入器不阻塞主线程。

    性能目标：
    - 100 个事件应在 <10ms 内完成发射（vs 同步版本 >300ms）
    - 单事件延迟 <0.1ms（vs 同步版本 3-11ms）
    """
    # 准备临时文件
    with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
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
    with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
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
        json.loads(line)
        for line in writer.file_path.read_text(encoding="utf-8").splitlines()
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
    with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
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


def test_emit_trace_performance():
    """验证 emit_trace 函数的性能优化。

    目标：
    - emit_trace 快速路径（无钩子时）应 <0.001ms
    - emit_trace 异步写入应 <0.1ms
    """
    # 清空所有钩子
    clear_trace_hooks()

    # 测试快速路径（无钩子且无写入器）
    start = time.perf_counter()
    for i in range(1000):
        emit_trace({"type": "perf_test", "index": i})
    elapsed_fast = time.perf_counter() - start

    # 验证：快速路径应极快（<1ms for 1000 events）
    assert elapsed_fast < 0.001, f"快速路径过慢: {elapsed_fast}s（预期 <0.001s）"

    # 测试异步写入路径
    with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
        test_file = Path(f.name)

    # 创建异步写入器并启动
    writer = AsyncTraceWriter(batch_interval=0.1, batch_size=50)
    writer.start(test_file)

    # 测量异步写入路径
    start = time.perf_counter()
    for i in range(100):
        emit_trace({"type": "async_perf_test", "index": i})
    elapsed_async = time.perf_counter() - start

    # 验证：异步路径应 <10ms（vs 同步版本 >300ms）
    assert elapsed_async < 0.01, f"异步路径过慢: {elapsed_async}s（预期 <0.01s）"

    # 清理
    writer.shutdown()

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
    with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
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
