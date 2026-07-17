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


def assert_two_medians_within_ratio(
    med_a: float,
    med_b: float,
    *,
    max_ratio: float = 6.0,
    msg: str = "",
) -> None:
    """两次独立测得的 median 相对比不超过 ``max_ratio``（宽松，抑 CI 抖动误报）。"""
    lo, hi = sorted((float(med_a), float(med_b)))
    if lo < 1e-9:
        return
    ratio = hi / lo
    assert ratio <= max_ratio, (
        msg or f"median ratio too high: {ratio:.2f} (lo={lo:.4f} hi={hi:.4f})"
    )


def tracemalloc_peak_diff_mb(run: Callable[[], Any]) -> float:
    """在 ``reset_peak`` 之后执行 ``run``，返回该段 traced 分配峰值（MiB，近似）。

    非「前后 RSS 差分」；名称保留以兼容既有合成用例（如 S6）。
    """
    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        run()
        _cur, peak = tracemalloc.get_traced_memory()
        return peak / (1024 * 1024)
    finally:
        tracemalloc.stop()
