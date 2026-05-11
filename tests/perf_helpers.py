"""合成性能测试辅助：中位数计时与可选 tracemalloc。"""

from __future__ import annotations

import tracemalloc
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

T = TypeVar("T")


def median_wall_seconds(runs: int, fn: Callable[[], Any]) -> float:
    """同步函数重复执行，返回 wall time 中位数（秒）。"""
    import time

    times: list[float] = []
    for _ in range(max(1, runs)):
        t0 = time.perf_counter()
        fn()
        times.append(time.perf_counter() - t0)
    times.sort()
    return times[len(times) // 2]


async def median_wall_seconds_async(runs: int, fn: Callable[[], Coroutine[Any, Any, Any]]) -> float:
    """异步协程重复执行，返回 wall time 中位数（秒）。"""
    import time

    times: list[float] = []
    for _ in range(max(1, runs)):
        t0 = time.perf_counter()
        await fn()
        times.append(time.perf_counter() - t0)
    times.sort()
    return times[len(times) // 2]


def tracemalloc_peak_diff_mb(run: Callable[[], Any]) -> float:
    """执行 run 前后 tracemalloc 当前峰值差（MB，近似）。"""
    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        run()
        _cur, peak = tracemalloc.get_traced_memory()
        return peak / (1024 * 1024)
    finally:
        tracemalloc.stop()
