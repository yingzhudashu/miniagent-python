"""性能测试辅助函数（扩展版）

提供性能测试所需的辅助工具：
- 时间采样
- 内存快照
- 延迟分解报告
- CPU 时间测量
"""

from __future__ import annotations

import gc
import os
import sys
import time
import tracemalloc
from collections.abc import Callable, Coroutine
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, TypeVar

T = TypeVar("T")


# =============================================================================
# 基础计时函数（继承自原 perf_helpers.py）
# =============================================================================


def median_wall_seconds(runs: int, fn: Callable[[], Any]) -> float:
    """同步函数重复执行，返回 wall time 中位数（秒）。"""
    times: list[float] = []
    for _ in range(max(1, runs)):
        t0 = time.perf_counter()
        fn()
        times.append(time.perf_counter() - t0)
    times.sort()
    return times[len(times) // 2]


async def median_wall_seconds_async(
    runs: int, fn: Callable[[], Coroutine[Any, Any, Any]]
) -> float:
    """异步协程重复执行，返回 wall time 中位数（秒）。"""
    times: list[float] = []
    for _ in range(max(1, runs)):
        t0 = time.perf_counter()
        await fn()
        times.append(time.perf_counter() - t0)
    times.sort()
    return times[len(times) // 2]


# =============================================================================
# 扩展函数
# =============================================================================


def cpu_time_sample() -> float:
    """获取当前进程 CPU 时间（秒）。

    使用 time.process_time() 返回 CPU 时间，
    不包含睡眠时间，适合测量纯 CPU 开销。
    """
    return time.process_time()


@contextmanager
def cpu_time_section(name: str = ""):
    """测量代码段 CPU 时间的上下文管理器。

    Args:
        name: 代码段名称（用于报告）

    Usage:
        with cpu_time_section("token_calculation"):
            calculate_tokens()
    """
    start = cpu_time_sample()
    yield
    end = cpu_time_sample()
    elapsed_ms = (end - start) * 1000
    if name:
        print(f"[{name}] CPU time: {elapsed_ms:.2f}ms")


@dataclass
class MemorySnapshot:
    """内存快照数据"""

    timestamp: float
    current_mb: float
    peak_mb: float
    allocations: int = 0
    details: dict[str, float] = field(default_factory=dict)


def memory_snapshot(label: str = "") -> MemorySnapshot:
    """获取当前内存状态快照。

    Args:
        label: 快照标签（可选）

    Returns:
        MemorySnapshot 包含当前和峰值内存使用
    """
    if not tracemalloc.is_tracing():
        tracemalloc.start()

    current, peak = tracemalloc.get_traced_memory()
    snapshot = tracemalloc.take_snapshot()

    # 获取前 5 个内存占用最大的分配
    top_stats = snapshot.statistics("lineno")[:5]
    details = {}
    for stat in top_stats:
        key = f"{stat.traceback.lineno}:{os.path.basename(stat.traceback.filename)}"
        details[key] = stat.size / 1024  # KB

    return MemorySnapshot(
        timestamp=time.monotonic(),
        current_mb=current / (1024 * 1024),
        peak_mb=peak / (1024 * 1024),
        allocations=len(snapshot.statistics("lineno")),
        details=details,
    )


@contextmanager
def memory_section(name: str = "", show_details: bool = False):
    """测量代码段内存增长的上下文管理器。

    Args:
        name: 代码段名称
        show_details: 是否显示详细分配信息

    Usage:
        with memory_section("session_load", show_details=True):
            load_session_data()
    """
    gc.collect()  # 强制 GC 以获得准确测量
    tracemalloc.reset_peak()
    before = memory_snapshot()

    yield

    gc.collect()
    after = memory_snapshot()

    delta_mb = after.current_mb - before.current_mb
    peak_delta = after.peak_mb - before.peak_mb

    if name:
        print(f"\n[{name}] Memory:")
        print(f"  Delta: {delta_mb:.2f}MB")
        print(f"  Peak delta: {peak_delta:.2f}MB")

        if show_details and after.details:
            print("  Top allocations:")
            for loc, size_kb in sorted(
                after.details.items(), key=lambda x: x[1], reverse=True
            )[:3]:
                print(f"    {loc}: {size_kb:.1f}KB")


@dataclass
class LatencyBreakdown:
    """延迟分解报告"""

    total_ms: float
    components: dict[str, float] = field(default_factory=dict)

    def add_component(self, name: str, latency_ms: float) -> None:
        """添加延迟组件"""
        self.components[name] = latency_ms

    def report(self) -> str:
        """生成报告字符串"""
        lines = [f"Total: {self.total_ms:.2f}ms"]
        for name, latency in sorted(
            self.components.items(), key=lambda x: x[1], reverse=True
        ):
            pct = latency / self.total_ms * 100 if self.total_ms > 0 else 0
            lines.append(f"  {name}: {latency:.2f}ms ({pct:.1f}%)")
        return "\n".join(lines)


@contextmanager
def latency_breakdown_section(name: str = ""):
    """测量代码段延迟的上下文管理器，返回 LatencyBreakdown 对象。

    Usage:
        with latency_breakdown_section("agent_run") as breakdown:
            with breakdown.measure("llm_call"):
                await llm_call()
            with breakdown.measure("tool_exec"):
                await execute_tools()
        print(breakdown.report())
    """
    breakdown = LatencyBreakdown(total_ms=0)
    start = time.perf_counter()

    # 添加嵌套测量方法
    def measure(component_name: str):
        return latency_component(component_name, breakdown)

    breakdown.measure = measure

    yield breakdown

    end = time.perf_counter()
    breakdown.total_ms = (end - start) * 1000

    if name:
        print(f"\n[{name}] Latency breakdown:")
        print(breakdown.report())


@contextmanager
def latency_component(name: str, breakdown: LatencyBreakdown):
    """测量单个延迟组件"""
    start = time.perf_counter()
    yield
    end = time.perf_counter()
    breakdown.add_component(name, (end - start) * 1000)


def tracemalloc_peak_diff_mb(run: Callable[[], Any]) -> float:
    """测量代码段内存峰值增长（MB）。

    在 reset_peak 之后执行 run，返回该段 traced 分配峰值（MiB）。
    """
    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        run()
        _cur, peak = tracemalloc.get_traced_memory()
        return peak / (1024 * 1024)
    finally:
        tracemalloc.stop()


async def tracemalloc_peak_diff_mb_async(
    run: Callable[[], Coroutine[Any, Any, Any]]
) -> float:
    """异步版本：测量代码段内存峰值增长（MB）。"""
    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        await run()
        _cur, peak = tracemalloc.get_traced_memory()
        return peak / (1024 * 1024)
    finally:
        tracemalloc.stop()


def assert_two_medians_within_ratio(
    med_a: float,
    med_b: float,
    *,
    max_ratio: float = 6.0,
    msg: str = "",
) -> None:
    """两次独立测得的 median 相对比不超过 max_ratio。

    用于宽松比较，抑制 CI 抖动误报。
    """
    lo, hi = sorted((float(med_a), float(med_b)))
    if lo < 1e-9:
        return
    ratio = hi / lo
    assert ratio <= max_ratio, (
        msg or f"median ratio too high: {ratio:.2f} (lo={lo:.4f} hi={hi:.4f})"
    )


def compare_performance(
    baseline_fn: Callable[[], Any],
    optimized_fn: Callable[[], Any],
    name: str = "",
    runs: int = 5,
    max_slowdown_ratio: float = 1.5,
) -> dict[str, float]:
    """比较基准实现和优化实现的性能。

    Args:
        baseline_fn: 基准实现
        optimized_fn: 优化实现
        name: 测试名称
        runs: 运行次数
        max_slowdown_ratio: 最大允许的慢化比率

    Returns:
        包含 baseline_time, optimized_time, improvement_ratio 的字典
    """
    baseline_time = median_wall_seconds(runs, baseline_fn)
    optimized_time = median_wall_seconds(runs, optimized_fn)

    if baseline_time > 0:
        improvement = baseline_time / optimized_time
    else:
        improvement = 1.0

    result = {
        "baseline_ms": baseline_time * 1000,
        "optimized_ms": optimized_time * 1000,
        "improvement": improvement,
    }

    if name:
        print(f"\n[{name}] Performance comparison:")
        print(f"  Baseline: {result['baseline_ms']:.2f}ms")
        print(f"  Optimized: {result['optimized_ms']:.2f}ms")
        if improvement >= 1:
            print(f"  Speedup: {improvement:.2f}x")
        else:
            print(f"  Slowdown: {1/improvement:.2f}x")

    # 验证没有显著退化
    assert improvement >= 1 / max_slowdown_ratio, (
        f"优化实现不应比基准慢超过 {max_slowdown_ratio}x"
    )

    return result


__all__ = [
    "median_wall_seconds",
    "median_wall_seconds_async",
    "cpu_time_sample",
    "cpu_time_section",
    "MemorySnapshot",
    "memory_snapshot",
    "memory_section",
    "LatencyBreakdown",
    "latency_breakdown_section",
    "latency_component",
    "tracemalloc_peak_diff_mb",
    "tracemalloc_peak_diff_mb_async",
    "assert_two_medians_within_ratio",
    "compare_performance",
]