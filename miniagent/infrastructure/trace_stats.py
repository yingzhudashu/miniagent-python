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
import re
from collections import defaultdict
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
    return sorted(trace_dir.glob(f"trace-{date}-pid*.jsonl"), key=lambda p: p.name)


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
    trace_files = get_trace_files(date)
    if not trace_files:
        return []

    events = []
    for trace_file in trace_files:
        try:
            with trace_file.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                        # 过滤条件
                        if session_key and event.get("session_key") != session_key:
                            continue
                        if event_type and event.get("type") != event_type:
                            continue
                        events.append(event)
                    except json.JSONDecodeError:
                        continue
        except OSError:
            continue

    return events


def _trace_file_date(trace_file: Path) -> str | None:
    """从基础文件或 pid 分片文件名中解析 YYYY-MM-DD。"""
    match = _TRACE_FILE_RE.match(trace_file.name)
    if not match:
        return None
    return match.group(1)


def compute_tool_stats(events: list[dict[str, Any]]) -> dict[str, Any]:
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
    # tool.end 自带 duration_ms 与 success，直接聚合；
    # tool.start 仅用于存在性/配对校验（按 tool_call_id），不参与时延计算。
    tool_stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "total_ms": 0, "success": 0, "fail": 0}
    )

    for event in events:
        event_type = event.get("type", "")

        if event_type == EVENT_TOOL_END:
            tool_name = event.get("tool", "")
            duration_ms = event.get("duration_ms", 0)
            success = event.get("success", True)

            stats = tool_stats[tool_name]
            stats["count"] += 1
            stats["total_ms"] += duration_ms
            if success:
                stats["success"] += 1
            else:
                stats["fail"] += 1

    # 计算平均值和成功率
    result = {"tools": {}, "slow_tools": [], "failed_tools": []}
    slow_threshold = get_config("self_optimization.min_duration_ms_threshold", 2000)

    for tool_name, stats in tool_stats.items():
        if stats["count"] > 0:
            avg_ms = round(stats["total_ms"] / stats["count"], 1)
            success_rate = round(stats["success"] / stats["count"], 3)
            result["tools"][tool_name] = {
                "count": stats["count"],
                "avg_ms": avg_ms,
                "success_rate": success_rate,
            }

            # 慢工具标记
            if avg_ms >= slow_threshold:
                result["slow_tools"].append({
                    "name": tool_name,
                    "avg_ms": avg_ms,
                    "count": stats["count"],
                })

            # 失败率高工具标记
            if success_rate < 0.95:
                result["failed_tools"].append({
                    "name": tool_name,
                    "success_rate": success_rate,
                    "fail_count": stats["fail"],
                })

    # 按平均时延排序慢工具
    result["slow_tools"].sort(key=lambda x: x["avg_ms"], reverse=True)

    return result


def compute_llm_stats(events: list[dict[str, Any]]) -> dict[str, Any]:
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
    request_count = 0
    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_messages = 0
    total_tools = 0
    # 按 phase（plan/exec/classify/reflect/clarify…）分组，便于定位各阶段调用次数与 token。
    phase_stats: dict[str, dict[str, int]] = defaultdict(
        lambda: {"request_count": 0, "prompt_tokens": 0, "completion_tokens": 0}
    )

    for event in events:
        event_type = event.get("type", "")
        phase = event.get("phase") or "unknown"

        if event_type == EVENT_LLM_REQUEST:
            request_count += 1
            total_messages += event.get("message_count", 0)
            total_tools += event.get("tool_count", 0)
            phase_stats[phase]["request_count"] += 1

        elif event_type == EVENT_LLM_RESPONSE:
            usage = event.get("usage", {})
            if usage:
                prompt = usage.get("prompt_tokens", 0) or 0
                completion = usage.get("completion_tokens", 0) or 0
                total_prompt_tokens += prompt
                total_completion_tokens += completion
                phase_stats[phase]["prompt_tokens"] += prompt
                phase_stats[phase]["completion_tokens"] += completion

    result = {
        "request_count": request_count,
        "total_tokens": {
            "prompt": total_prompt_tokens,
            "completion": total_completion_tokens,
        },
        "by_phase": {phase: dict(stats) for phase, stats in phase_stats.items()},
    }

    if request_count > 0:
        result["avg_messages"] = round(total_messages / request_count, 1)
        result["avg_tools"] = round(total_tools / request_count, 1)

    return result


def compute_error_stats(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
    error_counts: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "tools": set(), "is_user_error": False}
    )

    for event in events:
        event_type = event.get("type", "")
        if event_type in (EVENT_ERROR_COLLECT, EVENT_TOOL_ERROR):
            error_type = event.get("error_type", "Unknown")
            tool_name = event.get("tool_name") or event.get("tool", "")
            is_user_error = event.get("is_user_error", False)

            stats = error_counts[error_type]
            stats["count"] += 1
            if tool_name:
                stats["tools"].add(tool_name)
            if is_user_error:
                stats["is_user_error"] = True

    result = []
    for error_type, stats in sorted(
        error_counts.items(), key=lambda x: x[1]["count"], reverse=True
    ):
        result.append({
            "type": error_type,
            "count": stats["count"],
            "tools": sorted(stats["tools"]),
            "is_user_error": stats["is_user_error"],
        })

    return result


def compute_memory_stats(events: list[dict[str, Any]]) -> dict[str, Any]:
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
    read_count = 0
    total_duration = 0
    layer_counts: dict[str, int] = defaultdict(int)
    total_chars = 0
    cache_hits = 0

    for event in events:
        if event.get("type") == EVENT_MEMORY_READ:
            read_count += 1
            total_duration += event.get("duration_ms", 0)

            # 统计各层读取分布
            layer = event.get("layer", "unknown")
            layer_counts[layer] += 1

            # 统计加载字符数
            total_chars += event.get("chars_loaded", 0)

            # 统计缓存命中
            if event.get("cache_hit", False):
                cache_hits += 1

    result = {"read_count": read_count}

    if read_count > 0:
        result["avg_duration_ms"] = round(total_duration / read_count, 1)
        result["layer_distribution"] = dict(layer_counts)
        result["avg_chars_loaded"] = round(total_chars / read_count)
        result["cache_hit_rate"] = round(cache_hits / read_count, 3)

    return result


def compute_context_stats(events: list[dict[str, Any]]) -> dict[str, Any]:
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
    compress_count = 0
    total_duration = 0
    total_tokens_before = 0
    total_tokens_after = 0

    for event in events:
        if event.get("type") == EVENT_CONTEXT_COMPRESS:
            compress_count += 1
            total_duration += event.get("duration_ms", 0)
            total_tokens_before += event.get("tokens_before", 0)
            total_tokens_after += event.get("tokens_after", 0)

    result = {"compress_count": compress_count}

    if compress_count > 0:
        result["avg_duration_ms"] = round(total_duration / compress_count, 1)
        result["avg_tokens_before"] = round(total_tokens_before / compress_count)
        result["avg_tokens_after"] = round(total_tokens_after / compress_count)

        if total_tokens_before > 0:
            result["compress_ratio"] = round(total_tokens_after / total_tokens_before, 3)

        # 计算节省的token总数
        result["total_tokens_saved"] = total_tokens_before - total_tokens_after

    return result


def compute_embedding_stats(events: list[dict[str, Any]]) -> dict[str, Any]:
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
    cache_hit_count = 0
    api_call_count = 0
    total_api_latency = 0

    for event in events:
        event_type = event.get("type")

        if event_type == EVENT_EMBEDDING_CACHE_HIT:
            cache_hit_count += 1

        elif event_type == EVENT_EMBEDDING_API_CALL:
            api_call_count += 1
            total_api_latency += event.get("duration_ms", 0)

    total_requests = cache_hit_count + api_call_count
    result = {
        "cache_hit_count": cache_hit_count,
        "api_call_count": api_call_count
    }

    if total_requests > 0:
        result["cache_hit_rate"] = round(cache_hit_count / total_requests, 3)

    if api_call_count > 0:
        result["avg_api_latency_ms"] = round(total_api_latency / api_call_count, 1)

        # 成本估算：假设每次API调用成本 $0.0001
        # 根据 OpenAI text-embedding-3-small 价格：$0.00002 per 1K tokens
        # 平均每次调用约 500 tokens，成本约 $0.0001
        result["estimated_cost_saved"] = f"${cache_hit_count * 0.0001:.4f}"

    return result


def generate_daily_report(date: str | None = None) -> dict[str, Any]:
    """生成每日 Trace 统计报告。

    Args:
        date: 日期字符串（YYYY-MM-DD），默认今天

    Returns:
        统计报告字典
    """
    if date is None:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    events = load_trace_events(date)

    if not events:
        return {
            "date": date,
            "total_events": 0,
            "message": "无 trace 数据",
        }

    # 基础统计
    sessions = set()
    for event in events:
        session_key = event.get("session_key")
        if session_key:
            sessions.add(session_key)

    report = {
        "date": date,
        "total_events": len(events),
        "sessions": len(sessions),
        "session_list": sorted(sessions),
    }

    # 各维度统计
    report["llm"] = compute_llm_stats(events)
    report["tools"] = compute_tool_stats(events)
    report["errors"] = compute_error_stats(events)
    report["memory"] = compute_memory_stats(events)
    report["context"] = compute_context_stats(events)
    report["embedding"] = compute_embedding_stats(events)

    # 摘要文本
    llm_count = report["llm"].get("request_count", 0)
    tool_count = sum(t.get("count", 0) for t in report["tools"].get("tools", {}).values())
    error_count = sum(e.get("count", 0) for e in report["errors"])
    memory_count = report["memory"].get("read_count", 0)
    context_count = report["context"].get("compress_count", 0)

    report["summary"] = (
        f"LLM调用 {llm_count} 次，工具调用 {tool_count} 次，"
        f"记忆读取 {memory_count} 次，上下文压缩 {context_count} 次，"
        f"错误 {error_count} 次，会话 {len(sessions)} 个"
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
    trace_dir = get_trace_output_dir()
    if not trace_dir.exists():
        return 0

    removed_total = 0
    for trace_file in sorted(trace_dir.glob("trace-*.jsonl")):
        if not _TRACE_FILE_RE.match(trace_file.name):
            continue
        kept: list[str] = []
        removed = 0
        try:
            with open(trace_file, encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        event = json.loads(stripped)
                        if event.get("session_key") == session_key:
                            removed += 1
                            continue
                    except json.JSONDecodeError:
                        pass
                    kept.append(stripped)
            if removed:
                if kept:
                    with open(trace_file, "w", encoding="utf-8") as f:
                        f.write("\n".join(kept) + "\n")
                else:
                    trace_file.unlink(missing_ok=True)
                removed_total += removed
        except OSError:
            continue

    if removed_total > 0:
        _logger.debug("已从 trace 文件移除 session %s 的 %d 条事件", session_key, removed_total)
    return removed_total


__all__ = [
    "get_trace_output_dir",
    "get_trace_files",
    "load_trace_events",
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
