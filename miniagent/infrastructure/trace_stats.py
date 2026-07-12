"""Trace 统计与报告生成。

分析 trace.jsonl 文件，生成性能指标统计、错误汇总、时延分布等报告。
支持按时间范围、会话、事件类型等多维度统计。

功能：
- 按时间范围加载 trace 事件
- 计算各类事件的性能指标（时延分布、成功率等）
- 生成每日 Trace 摘要报告
- 支持按会话聚合统计

输出报告格式：
{
  "date": "2026-06-05",
  "total_events": 1234,
  "sessions": 10,
  "summary": {
    "llm": { "avg_duration_ms": 500, "total_tokens": 10000 },
    "tools": { "avg_duration_ms": 150, "success_rate": 0.95 },
    "memory": { "avg_duration_ms": 50 },
    "context": { "compress_count": 5 }
  },
  "errors": [
    { "type": "TimeoutError", "count": 3, "tools": ["web_search"] }
  ],
  "slow_tools": [
    { "name": "web_search", "avg_ms": 2000, "count": 10 }
  ]
}

详见 docs/ENGINEERING.md（Trace 系统）。
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
from collections import defaultdict
from collections.abc import Iterable, Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from miniagent.infrastructure.json_config import get_config
from miniagent.infrastructure.logger import get_logger
from miniagent.infrastructure.trace_events import (
    EVENT_CONTEXT_COMPRESS,
    EVENT_EMBEDDING_API_CALL,
    EVENT_EMBEDDING_CACHE_HIT,
    EVENT_ERROR_COLLECT,
    EVENT_LLM_REQUEST,
    EVENT_LLM_RESPONSE,
    EVENT_MEMORY_READ,
    EVENT_TOOL_END,
    EVENT_TOOL_ERROR,
)

_logger = get_logger(__name__)

_TRACE_FILE_RE = re.compile(r"^trace-(\d{4}-\d{2}-\d{2})(?:-pid\d+)?\.jsonl$")


def get_trace_output_dir() -> Path:
    """获取 Trace 输出目录。

    优先级：
    1. 配置 trace.output_dir
    2. 默认 workspaces/logs
    """
    config_dir = get_config("trace.output_dir", None)
    if config_dir:
        return Path(config_dir)

    return Path("workspaces/logs")


def get_trace_files(date: str | None = None) -> list[Path]:
    """获取指定日期的所有 trace 文件分片。

    异步写入器会为每个进程写入 ``trace-YYYY-MM-DD-pid{pid}.jsonl``，
    统计侧读取当天全部 pid 分片，避免日报漏掉并行进程数据。
    """
    if date is None:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    trace_dir = get_trace_output_dir()
    if not trace_dir.exists():
        return []
    base_name = f"trace-{date}.jsonl"
    return sorted(
        (
            path
            for path in trace_dir.iterdir()
            if path.is_file() and _trace_file_date(path) == date
        ),
        key=lambda path: (path.name != base_name, path.name),
    )


def load_trace_events(
    date: str | None = None,
    session_key: str | None = None,
    event_type: str | None = None,
) -> list[dict[str, Any]]:
    """加载 trace 事件，支持多维度过滤。

    Args:
        date: 日期字符串（YYYY-MM-DD），默认今天
        session_key: 按会话过滤（可选）
        event_type: 按事件类型过滤（可选）

    Returns:
        事件列表
    """
    return list(
        iter_trace_events(
            date,
            session_key=session_key,
            event_type=event_type,
        )
    )


def iter_trace_events(
    date: str | None = None,
    session_key: str | None = None,
    event_type: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield matching trace events without retaining a whole day in memory."""
    trace_files = get_trace_files(date)
    for trace_file in trace_files:
        try:
            with trace_file.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                        if not isinstance(event, dict):
                            continue
                        # 过滤条件
                        if session_key and event.get("session_key") != session_key:
                            continue
                        if event_type and event.get("type") != event_type:
                            continue
                        yield event
                    except json.JSONDecodeError:
                        continue
        except OSError:
            continue

def _trace_file_date(trace_file: Path) -> str | None:
    """从基础文件或 pid 分片文件名中解析 YYYY-MM-DD。"""
    match = _TRACE_FILE_RE.match(trace_file.name)
    if not match:
        return None
    return match.group(1)


def compute_tool_stats(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """计算工具执行统计。

    Args:
        events: 包含 tool.start/tool.end 的事件列表

    Returns:
        统计结果：
        {
          "tools": {
            "read_file": { "count": 10, "avg_ms": 50, "success_rate": 1.0 },
            ...
          },
          "slow_tools": [...],
          "failed_tools": [...]
        }
    """
    return _aggregate_trace_events(events).tool_report()


def compute_llm_stats(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """计算 LLM 调用统计。

    Args:
        events: 包含 llm.request/llm.response 的事件列表

    Returns:
        统计结果：
        {
          "request_count": 10,
          "total_tokens": { "prompt": 5000, "completion": 2000 },
          "avg_messages": 5,
          "avg_tools": 3
        }
    """
    return _aggregate_trace_events(events).llm_report()


def _numeric_metric(value: Any) -> float | None:
    """Return a finite numeric metric while rejecting bools and malformed data."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) else None


def _token_metric(usage: dict[str, Any], primary: str, fallback: str) -> int | float:
    """Read one token counter using protocol-specific aliases."""
    value = _numeric_metric(usage.get(primary))
    if value is None:
        value = _numeric_metric(usage.get(fallback))
    return value if value is not None and value >= 0 else 0


def _nested_token_metric(
    usage: dict[str, Any],
    detail_keys: tuple[str, ...],
    metric_key: str,
) -> int | float:
    """Read a token detail from the first compatible protocol detail object."""
    for detail_key in detail_keys:
        details = usage.get(detail_key)
        if not isinstance(details, dict):
            continue
        value = _numeric_metric(details.get(metric_key))
        if value is not None and value >= 0:
            return value
    return 0


def _latency_summary(durations: list[float]) -> dict[str, float]:
    """Compute stable average and nearest-rank p50/p95 latency metrics."""
    ordered = sorted(durations)

    def nearest_rank(percentile: float) -> float:
        index = max(0, math.ceil(percentile * len(ordered)) - 1)
        return round(ordered[index], 1)

    return {
        "avg_duration_ms": round(sum(ordered) / len(ordered), 1),
        "p50_duration_ms": nearest_rank(0.50),
        "p95_duration_ms": nearest_rank(0.95),
    }


class _TraceStatsAccumulator:
    """Incrementally aggregate report metrics without retaining full events."""

    def __init__(self) -> None:
        self.total_events = 0
        self.sessions: set[str] = set()
        self.tool_stats: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"count": 0, "total_ms": 0, "success": 0, "fail": 0}
        )
        self.llm_request_count = 0
        self.llm_response_count = 0
        self.llm_failed_response_count = 0
        self.total_prompt_tokens: int | float = 0
        self.total_completion_tokens: int | float = 0
        self.total_cached_tokens: int | float = 0
        self.total_reasoning_tokens: int | float = 0
        self.total_messages = 0
        self.total_tools = 0
        self.llm_durations: list[float] = []
        self.phase_stats: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "request_count": 0,
                "response_count": 0,
                "failed_response_count": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cached_tokens": 0,
                "reasoning_tokens": 0,
                "message_count": 0,
                "tool_count": 0,
                "durations_ms": [],
            }
        )
        self.error_counts: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"count": 0, "tools": set(), "is_user_error": False}
        )
        self.memory_read_count = 0
        self.memory_total_duration = 0
        self.memory_layer_counts: dict[str, int] = defaultdict(int)
        self.memory_total_chars = 0
        self.memory_cache_hits = 0
        self.context_compress_count = 0
        self.context_total_duration = 0
        self.context_total_tokens_before = 0
        self.context_total_tokens_after = 0
        self.embedding_cache_hit_count = 0
        self.embedding_api_call_count = 0
        self.embedding_total_api_latency = 0

    def add(self, event: dict[str, Any]) -> None:
        """Consume one already-parsed trace event."""
        self.total_events += 1
        session_key = event.get("session_key")
        if isinstance(session_key, str) and session_key:
            self.sessions.add(session_key)

        event_type = event.get("type", "")
        if event_type == EVENT_TOOL_END:
            self._add_tool_end(event)
        elif event_type == EVENT_LLM_REQUEST:
            self._add_llm_request(event)
        elif event_type == EVENT_LLM_RESPONSE:
            self._add_llm_response(event)
        elif event_type in (EVENT_ERROR_COLLECT, EVENT_TOOL_ERROR):
            self._add_error(event)
        elif event_type == EVENT_MEMORY_READ:
            self._add_memory_read(event)
        elif event_type == EVENT_CONTEXT_COMPRESS:
            self._add_context_compress(event)
        elif event_type == EVENT_EMBEDDING_CACHE_HIT:
            self.embedding_cache_hit_count += 1
        elif event_type == EVENT_EMBEDDING_API_CALL:
            self.embedding_api_call_count += 1
            self.embedding_total_api_latency += event.get("duration_ms", 0)

    def _add_tool_end(self, event: dict[str, Any]) -> None:
        tool_name = event.get("tool", "")
        stats = self.tool_stats[tool_name]
        stats["count"] += 1
        stats["total_ms"] += event.get("duration_ms", 0)
        if event.get("success", True):
            stats["success"] += 1
        else:
            stats["fail"] += 1

    def _add_llm_request(self, event: dict[str, Any]) -> None:
        phase = event.get("phase") or "unknown"
        message_count = event.get("message_count", 0)
        tool_count = event.get("tool_count", 0)
        self.llm_request_count += 1
        self.total_messages += message_count
        self.total_tools += tool_count
        self.phase_stats[phase]["request_count"] += 1
        self.phase_stats[phase]["message_count"] += message_count
        self.phase_stats[phase]["tool_count"] += tool_count

    def _add_llm_response(self, event: dict[str, Any]) -> None:
        phase = event.get("phase") or "unknown"
        phase_stats = self.phase_stats[phase]
        self.llm_response_count += 1
        phase_stats["response_count"] += 1
        if event.get("failure_category"):
            self.llm_failed_response_count += 1
            phase_stats["failed_response_count"] += 1

        duration = _numeric_metric(event.get("duration_ms"))
        if duration is not None and duration >= 0:
            self.llm_durations.append(duration)
            phase_stats["durations_ms"].append(duration)

        usage = event.get("usage", {})
        if not isinstance(usage, dict):
            return
        prompt = _token_metric(usage, "prompt_tokens", "input_tokens")
        completion = _token_metric(usage, "completion_tokens", "output_tokens")
        cached = _nested_token_metric(
            usage,
            ("prompt_tokens_details", "input_tokens_details"),
            "cached_tokens",
        )
        reasoning = _nested_token_metric(
            usage,
            ("completion_tokens_details", "output_tokens_details"),
            "reasoning_tokens",
        )
        self.total_prompt_tokens += prompt
        self.total_completion_tokens += completion
        self.total_cached_tokens += cached
        self.total_reasoning_tokens += reasoning
        phase_stats["prompt_tokens"] += prompt
        phase_stats["completion_tokens"] += completion
        phase_stats["cached_tokens"] += cached
        phase_stats["reasoning_tokens"] += reasoning

    def _add_error(self, event: dict[str, Any]) -> None:
        error_type = event.get("error_type", "Unknown")
        stats = self.error_counts[error_type]
        stats["count"] += 1
        tool_name = event.get("tool_name") or event.get("tool", "")
        if tool_name:
            stats["tools"].add(tool_name)
        if event.get("is_user_error", False):
            stats["is_user_error"] = True

    def _add_memory_read(self, event: dict[str, Any]) -> None:
        self.memory_read_count += 1
        self.memory_total_duration += event.get("duration_ms", 0)
        self.memory_layer_counts[event.get("layer", "unknown")] += 1
        self.memory_total_chars += event.get("chars_loaded", 0)
        if event.get("cache_hit", False):
            self.memory_cache_hits += 1

    def _add_context_compress(self, event: dict[str, Any]) -> None:
        self.context_compress_count += 1
        self.context_total_duration += event.get("duration_ms", 0)
        self.context_total_tokens_before += event.get(
            "before_tokens",
            event.get("tokens_before", 0),
        )
        self.context_total_tokens_after += event.get(
            "after_tokens",
            event.get("tokens_after", 0),
        )

    def tool_report(self) -> dict[str, Any]:
        """Finalize tool counters using the existing public report contract."""
        result: dict[str, Any] = {
            "tools": {},
            "slow_tools": [],
            "failed_tools": [],
        }
        slow_threshold = get_config(
            "self_optimization.min_duration_ms_threshold",
            2000,
        )
        for tool_name, stats in self.tool_stats.items():
            if stats["count"] <= 0:
                continue
            avg_ms = round(stats["total_ms"] / stats["count"], 1)
            success_rate = round(stats["success"] / stats["count"], 3)
            result["tools"][tool_name] = {
                "count": stats["count"],
                "avg_ms": avg_ms,
                "success_rate": success_rate,
            }
            if avg_ms >= slow_threshold:
                result["slow_tools"].append({
                    "name": tool_name,
                    "avg_ms": avg_ms,
                    "count": stats["count"],
                })
            if success_rate < 0.95:
                result["failed_tools"].append({
                    "name": tool_name,
                    "success_rate": success_rate,
                    "fail_count": stats["fail"],
                })
        result["slow_tools"].sort(key=lambda item: item["avg_ms"], reverse=True)
        return result

    def llm_report(self) -> dict[str, Any]:
        """Finalize protocol-neutral LLM counters and exact latency percentiles."""
        by_phase: dict[str, dict[str, Any]] = {}
        for phase, stats in self.phase_stats.items():
            phase_durations = stats["durations_ms"]
            phase_result = {
                key: value
                for key, value in stats.items()
                if key not in {"durations_ms", "message_count", "tool_count"}
            }
            phase_response_count = phase_result["response_count"]
            phase_result["error_rate"] = (
                round(
                    phase_result["failed_response_count"] / phase_response_count,
                    3,
                )
                if phase_response_count
                else 0.0
            )
            phase_request_count = phase_result["request_count"]
            phase_result["avg_messages"] = (
                round(stats["message_count"] / phase_request_count, 1)
                if phase_request_count
                else 0.0
            )
            phase_result["avg_tools"] = (
                round(stats["tool_count"] / phase_request_count, 1)
                if phase_request_count
                else 0.0
            )
            if phase_request_count:
                phase_result["avg_prompt_tokens"] = round(
                    phase_result["prompt_tokens"] / phase_request_count,
                    1,
                )
                phase_result["avg_completion_tokens"] = round(
                    phase_result["completion_tokens"] / phase_request_count,
                    1,
                )
            phase_prompt_tokens = phase_result["prompt_tokens"]
            phase_result["cached_token_rate"] = (
                round(phase_result["cached_tokens"] / phase_prompt_tokens, 3)
                if phase_prompt_tokens
                else 0.0
            )
            if phase_durations:
                phase_result.update(_latency_summary(phase_durations))
            by_phase[phase] = phase_result

        result: dict[str, Any] = {
            "request_count": self.llm_request_count,
            "response_count": self.llm_response_count,
            "failed_response_count": self.llm_failed_response_count,
            "error_rate": (
                round(
                    self.llm_failed_response_count / self.llm_response_count,
                    3,
                )
                if self.llm_response_count
                else 0.0
            ),
            "total_tokens": {
                "prompt": self.total_prompt_tokens,
                "completion": self.total_completion_tokens,
                "cached": self.total_cached_tokens,
                "reasoning": self.total_reasoning_tokens,
                "total": self.total_prompt_tokens + self.total_completion_tokens,
            },
            "by_phase": by_phase,
        }
        if self.llm_request_count > 0:
            result["avg_messages"] = round(
                self.total_messages / self.llm_request_count,
                1,
            )
            result["avg_tools"] = round(
                self.total_tools / self.llm_request_count,
                1,
            )
            result["avg_prompt_tokens"] = round(
                self.total_prompt_tokens / self.llm_request_count,
                1,
            )
            result["avg_completion_tokens"] = round(
                self.total_completion_tokens / self.llm_request_count,
                1,
            )
        result["cached_token_rate"] = (
            round(self.total_cached_tokens / self.total_prompt_tokens, 3)
            if self.total_prompt_tokens
            else 0.0
        )
        if self.llm_durations:
            result.update(_latency_summary(self.llm_durations))
        return result

    def error_report(self) -> list[dict[str, Any]]:
        """Finalize grouped error counters."""
        return [
            {
                "type": error_type,
                "count": stats["count"],
                "tools": sorted(stats["tools"]),
                "is_user_error": stats["is_user_error"],
            }
            for error_type, stats in sorted(
                self.error_counts.items(),
                key=lambda item: item[1]["count"],
                reverse=True,
            )
        ]

    def memory_report(self) -> dict[str, Any]:
        """Finalize memory read counters."""
        result: dict[str, Any] = {"read_count": self.memory_read_count}
        if self.memory_read_count > 0:
            result["avg_duration_ms"] = round(
                self.memory_total_duration / self.memory_read_count,
                1,
            )
            result["layer_distribution"] = dict(self.memory_layer_counts)
            result["avg_chars_loaded"] = round(
                self.memory_total_chars / self.memory_read_count
            )
            result["cache_hit_rate"] = round(
                self.memory_cache_hits / self.memory_read_count,
                3,
            )
        return result

    def context_report(self) -> dict[str, Any]:
        """Finalize context compression counters."""
        result: dict[str, Any] = {
            "compress_count": self.context_compress_count,
        }
        if self.context_compress_count > 0:
            result["avg_duration_ms"] = round(
                self.context_total_duration / self.context_compress_count,
                1,
            )
            result["avg_tokens_before"] = round(
                self.context_total_tokens_before / self.context_compress_count
            )
            result["avg_tokens_after"] = round(
                self.context_total_tokens_after / self.context_compress_count
            )
            if self.context_total_tokens_before > 0:
                result["compress_ratio"] = round(
                    self.context_total_tokens_after
                    / self.context_total_tokens_before,
                    3,
                )
            result["total_tokens_saved"] = (
                self.context_total_tokens_before - self.context_total_tokens_after
            )
        return result

    def embedding_report(self) -> dict[str, Any]:
        """Finalize embedding cache and latency counters."""
        total_requests = (
            self.embedding_cache_hit_count + self.embedding_api_call_count
        )
        result: dict[str, Any] = {
            "cache_hit_count": self.embedding_cache_hit_count,
            "api_call_count": self.embedding_api_call_count,
        }
        if total_requests > 0:
            result["cache_hit_rate"] = round(
                self.embedding_cache_hit_count / total_requests,
                3,
            )
        if self.embedding_api_call_count > 0:
            result["avg_api_latency_ms"] = round(
                self.embedding_total_api_latency / self.embedding_api_call_count,
                1,
            )
            result["estimated_cost_saved"] = (
                f"${self.embedding_cache_hit_count * 0.0001:.4f}"
            )
        return result


def _aggregate_trace_events(
    events: Iterable[dict[str, Any]],
) -> _TraceStatsAccumulator:
    accumulator = _TraceStatsAccumulator()
    for event in events:
        accumulator.add(event)
    return accumulator


def aggregate_trace_stats(
    events: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Aggregate all report dimensions in one pass over an event iterable."""
    stats = _aggregate_trace_events(events)
    return {
        "total_events": stats.total_events,
        "sessions": len(stats.sessions),
        "session_list": sorted(stats.sessions),
        "llm": stats.llm_report(),
        "tools": stats.tool_report(),
        "errors": stats.error_report(),
        "memory": stats.memory_report(),
        "context": stats.context_report(),
        "embedding": stats.embedding_report(),
    }


def compute_error_stats(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """计算错误统计。

    Args:
        events: 包含 error.collect/tool.error 的事件列表

    Returns:
        错误汇总列表：
        [
          { "type": "TimeoutError", "count": 3, "tools": ["web_search"], "is_user_error": false },
          ...
        ]
    """
    return _aggregate_trace_events(events).error_report()


def compute_memory_stats(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """计算记忆操作统计。

    Args:
        events: 包含 memory.read 的事件列表

    Returns:
        统计结果：
        {
          "read_count": 10,
          "avg_duration_ms": 50,
          "layer_distribution": {"session_lt": 5, "agent_lt": 3},
          "avg_chars_loaded": 2000,
          "cache_hit_rate": 0.8
        }
    """
    return _aggregate_trace_events(events).memory_report()


def compute_context_stats(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """计算上下文管理统计。

    Args:
        events: 包含 context.compress 的事件列表

    Returns:
        统计结果：
        {
          "compress_count": 5,
          "avg_duration_ms": 100,
          "avg_tokens_before": 10000,
          "avg_tokens_after": 3000,
          "compress_ratio": 0.3,
          "total_tokens_saved": 35000
        }
    """
    return _aggregate_trace_events(events).context_report()


def compute_embedding_stats(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """计算Embedding缓存统计。

    Args:
        events: 包含 embedding.cache_hit/embedding.api_call 的事件列表

    Returns:
        统计结果：
        {
          "cache_hit_count": 50,
          "cache_hit_rate": 0.8,
          "api_call_count": 10,
          "avg_api_latency_ms": 150,
          "estimated_cost_saved": "$0.0050"
        }
    """
    return _aggregate_trace_events(events).embedding_report()


def generate_daily_report(date: str | None = None) -> dict[str, Any]:
    """生成每日 Trace 统计报告。

    Args:
        date: 日期字符串（YYYY-MM-DD），默认今天

    Returns:
        统计报告字典
    """
    if date is None:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    stats = _aggregate_trace_events(iter_trace_events(date))

    if stats.total_events == 0:
        return {
            "date": date,
            "total_events": 0,
            "message": "无 trace 数据",
        }

    report = {
        "date": date,
        "total_events": stats.total_events,
        "sessions": len(stats.sessions),
        "session_list": sorted(stats.sessions),
    }

    # 各维度统计
    report["llm"] = stats.llm_report()
    report["tools"] = stats.tool_report()
    report["errors"] = stats.error_report()
    report["memory"] = stats.memory_report()
    report["context"] = stats.context_report()
    report["embedding"] = stats.embedding_report()

    # 摘要文本
    llm_count = report["llm"].get("request_count", 0)
    tool_count = sum(t.get("count", 0) for t in report["tools"].get("tools", {}).values())
    error_count = sum(e.get("count", 0) for e in report["errors"])
    memory_count = report["memory"].get("read_count", 0)
    context_count = report["context"].get("compress_count", 0)

    report["summary"] = (
        f"LLM调用 {llm_count} 次，工具调用 {tool_count} 次，"
        f"记忆读取 {memory_count} 次，上下文压缩 {context_count} 次，"
        f"错误 {error_count} 次，会话 {len(stats.sessions)} 个"
    )

    return report


def save_report(report: dict[str, Any], output_dir: Path | None = None) -> Path:
    """保存统计报告到文件。

    Args:
        report: 统计报告字典
        output_dir: 输出目录（默认 workspaces/logs/reports）

    Returns:
        报告文件路径
    """
    if output_dir is None:
        output_dir = get_trace_output_dir() / "reports"

    output_dir.mkdir(parents=True, exist_ok=True)

    date = report.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    report_file = output_dir / f"trace-report-{date}.json"

    with report_file.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    _logger.info("Trace 报告已保存: %s", report_file)
    return report_file


def cleanup_old_traces(retention_days: int = 7) -> int:
    """清理过期的 trace 文件。

    Args:
        retention_days: 保留天数（默认 7）

    Returns:
        删除的文件数
    """
    trace_dir = get_trace_output_dir()
    if not trace_dir.exists():
        return 0

    cutoff_date = datetime.now(timezone.utc) - timedelta(days=retention_days)
    deleted = 0

    for trace_file in trace_dir.glob("trace-*.jsonl"):
        try:
            date_str = _trace_file_date(trace_file)
            if date_str is None:
                continue
            file_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if file_date < cutoff_date:
                trace_file.unlink()
                deleted += 1
        except (ValueError, OSError):
            continue

    if deleted > 0:
        _logger.info("清理过期 trace 文件: %d 个", deleted)

    return deleted


def remove_session_from_trace_files(session_key: str) -> int:
    """从 trace 目录下所有 jsonl 分片中移除指定 session 的事件行。

    Args:
        session_key: 会话标识

    Returns:
        移除的事件行总数
    """
    session_key = (session_key or "").strip()
    if not session_key:
        return 0
    from miniagent.infrastructure.tracing import exclude_trace_session

    active_file, removed_total = exclude_trace_session(session_key)
    active_resolved = active_file.resolve() if active_file is not None else None

    trace_dir = get_trace_output_dir()
    if not trace_dir.exists():
        return removed_total

    for trace_file in sorted(trace_dir.glob("trace-*.jsonl")):
        if not _TRACE_FILE_RE.match(trace_file.name):
            continue
        if active_resolved is not None and trace_file.resolve() == active_resolved:
            continue
        removed_total += _stream_remove_session_from_trace_file(trace_file, session_key)

    if removed_total > 0:
        _logger.debug("已从 trace 文件移除 session %s 的 %d 条事件", session_key, removed_total)
    return removed_total


def _stream_remove_session_from_trace_file(
    trace_file: Path,
    session_key: str,
) -> int:
    """Atomically filter one inactive shard with constant auxiliary memory."""
    temp_path: Path | None = None
    removed = 0
    kept = 0
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=trace_file.parent,
            prefix=f".{trace_file.name}.",
            suffix=".tmp",
            delete=False,
        ) as target:
            temp_path = Path(target.name)
            with trace_file.open(encoding="utf-8") as source:
                for line in source:
                    stripped = line.strip()
                    should_remove = False
                    if stripped:
                        try:
                            event = json.loads(stripped)
                            should_remove = (
                                isinstance(event, dict)
                                and event.get("session_key") == session_key
                            )
                        except json.JSONDecodeError:
                            pass
                    if should_remove:
                        removed += 1
                        continue
                    kept += 1
                    target.write(line if line.endswith("\n") else line + "\n")
        if not removed:
            temp_path.unlink(missing_ok=True)
            return 0
        if kept:
            os.replace(temp_path, trace_file)
        else:
            trace_file.unlink(missing_ok=True)
            temp_path.unlink(missing_ok=True)
        temp_path = None
        return removed
    except OSError:
        return 0
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


__all__ = [
    "get_trace_output_dir",
    "get_trace_files",
    "iter_trace_events",
    "load_trace_events",
    "aggregate_trace_stats",
    "compute_tool_stats",
    "compute_llm_stats",
    "compute_error_stats",
    "compute_memory_stats",
    "compute_context_stats",
    "compute_embedding_stats",
    "generate_daily_report",
    "save_report",
    "cleanup_old_traces",
    "remove_session_from_trace_files",
]
