"""Trace 系统性能测试。

验证异步写入器的性能优化效果：
- 非阻塞发射事件（vs 同步阻塞 3-11ms）
- 批量写入不丢数据
- 优雅关闭机制
"""
from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

from miniagent.infrastructure.tracing import (
    AsyncTraceWriter,
    clear_trace_hooks,
    emit_trace,
)


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
