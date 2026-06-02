"""MiniAgent Python — 轻量级性能指标采集

提供运行时性能指标采集，用于：
- 监控 Agent 执行性能
- 生成性能报告
- 支持性能回归检测

设计原则：
- 低开销：采集逻辑本身不影响性能
- 可选：默认关闭，通过配置启用
- 轻量：不依赖外部库，纯 Python 实现
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from miniagent.infrastructure.json_config import get_config


@dataclass
class LatencyRecord:
    """延迟记录"""

    name: str
    latency_ms: float
    timestamp: float = field(default_factory=time.monotonic)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class MetricSummary:
    """指标摘要"""

    name: str
    count: int
    total_ms: float
    avg_ms: float
    min_ms: float
    max_ms: float
    p50_ms: float
    p95_ms: float
    p99_ms: float


class PerformanceMetrics:
    """轻量级性能指标采集器

    Usage:
        metrics = PerformanceMetrics()

        # 记录 LLM 调用
        with metrics.measure("llm_call"):
            await llm_client.chat.completions.create(...)

        # 记录工具调用
        metrics.record_tool_call("web_search", 150.5)

        # 获取摘要
        summary = metrics.get_summary()
        print(summary)
    """

    def __init__(self, enabled: bool | None = None) -> None:
        """创建性能指标采集器

        Args:
            enabled: 是否启用（默认从配置读取）
        """
        if enabled is None:
            enabled = get_config("debug.perf_metrics", False)
        self._enabled = enabled
        self._records: list[LatencyRecord] = []
        self._start_time = time.monotonic()

    def is_enabled(self) -> bool:
        """检查是否启用"""
        return self._enabled

    def record_llm_call(
        self,
        latency_ms: float,
        tokens: int = 0,
        model: str = "",
    ) -> None:
        """记录 LLM 调用

        Args:
            latency_ms: 调用延迟（毫秒）
            tokens: Token 数量
            model: 模型名称
        """
        if not self._enabled:
            return
        self._records.append(
            LatencyRecord(
                name="llm_call",
                latency_ms=latency_ms,
                metadata={"tokens": tokens, "model": model},
            )
        )

    def record_tool_call(
        self,
        name: str,
        latency_ms: float,
        success: bool = True,
    ) -> None:
        """记录工具调用

        Args:
            name: 工具名称
            latency_ms: 调用延迟（毫秒）
            success: 是否成功
        """
        if not self._enabled:
            return
        self._records.append(
            LatencyRecord(
                name=f"tool_{name}",
                latency_ms=latency_ms,
                metadata={"success": success},
            )
        )

    def record_render_cycle(self, duration_ms: float) -> None:
        """记录渲染周期

        Args:
            duration_ms: 渲染时间（毫秒）
        """
        if not self._enabled:
            return
        self._records.append(
            LatencyRecord(
                name="render_cycle",
                latency_ms=duration_ms,
            )
        )

    def measure(self, name: str) -> _MeasureContext:
        """测量代码段延迟的上下文管理器

        Args:
            name: 操作名称

        Usage:
            with metrics.measure("token_calculation"):
                calculate_tokens()
        """
        return _MeasureContext(self, name)

    def add_record(
        self,
        name: str,
        latency_ms: float,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """添加延迟记录"""
        if not self._enabled:
            return
        self._records.append(
            LatencyRecord(
                name=name,
                latency_ms=latency_ms,
                metadata=metadata or {},
            )
        )

    def get_summary(self) -> dict[str, MetricSummary]:
        """获取各指标的摘要统计

        Returns:
            按名称分组的统计摘要字典
        """
        if not self._records:
            return {}

        # 按名称分组
        groups: dict[str, list[float]] = {}
        for record in self._records:
            if record.name not in groups:
                groups[record.name] = []
            groups[record.name].append(record.latency_ms)

        # 计算统计
        summaries: dict[str, MetricSummary] = {}
        for name, latencies in groups.items():
            latencies.sort()
            count = len(latencies)
            total = sum(latencies)
            avg = total / count if count > 0 else 0

            summaries[name] = MetricSummary(
                name=name,
                count=count,
                total_ms=total,
                avg_ms=avg,
                min_ms=latencies[0] if latencies else 0,
                max_ms=latencies[-1] if latencies else 0,
                p50_ms=latencies[count // 2] if latencies else 0,
                p95_ms=latencies[int(count * 0.95)] if latencies else 0,
                p99_ms=latencies[int(count * 0.99)] if latencies else 0,
            )

        return summaries

    def get_session_duration_ms(self) -> float:
        """获取会话总时长（毫秒）"""
        return (time.monotonic() - self._start_time) * 1000

    def to_json(self) -> str:
        """导出为 JSON 格式"""
        summary = self.get_summary()
        data = {
            "session_duration_ms": self.get_session_duration_ms(),
            "summaries": {
                name: {
                    "count": s.count,
                    "total_ms": s.total_ms,
                    "avg_ms": s.avg_ms,
                    "min_ms": s.min_ms,
                    "max_ms": s.max_ms,
                    "p50_ms": s.p50_ms,
                    "p95_ms": s.p95_ms,
                    "p99_ms": s.p99_ms,
                }
                for name, s in summary.items()
            },
            "records": [
                {
                    "name": r.name,
                    "latency_ms": r.latency_ms,
                    "timestamp": r.timestamp,
                    "metadata": r.metadata,
                }
                for r in self._records
            ],
        }
        return json.dumps(data, indent=2)

    def reset(self) -> None:
        """重置所有记录"""
        self._records.clear()
        self._start_time = time.monotonic()

    def __repr__(self) -> str:
        summary = self.get_summary()
        if not summary:
            return "PerformanceMetrics(empty)"
        lines = ["PerformanceMetrics:"]
        for name, s in summary.items():
            lines.append(f"  {name}: {s.count} calls, avg={s.avg_ms:.1f}ms")
        return "\n".join(lines)


class _MeasureContext:
    """测量上下文管理器"""

    def __init__(self, metrics: PerformanceMetrics, name: str) -> None:
        self._metrics = metrics
        self._name = name
        self._start = 0.0

    def __enter__(self) -> _MeasureContext:
        self._start = time.perf_counter()
        return self

    def __exit__(self, *args: Any) -> None:
        end = time.perf_counter()
        latency_ms = (end - self._start) * 1000
        self._metrics.add_record(self._name, latency_ms)


# 进程级全局指标实例（可选使用）
_global_metrics: PerformanceMetrics | None = None


def get_global_metrics() -> PerformanceMetrics:
    """获取全局性能指标实例"""
    global _global_metrics
    if _global_metrics is None:
        _global_metrics = PerformanceMetrics()
    return _global_metrics


def reset_global_metrics() -> None:
    """重置全局性能指标实例"""
    global _global_metrics
    if _global_metrics is not None:
        _global_metrics.reset()


__all__ = [
    "PerformanceMetrics",
    "LatencyRecord",
    "MetricSummary",
    "get_global_metrics",
    "reset_global_metrics",
]