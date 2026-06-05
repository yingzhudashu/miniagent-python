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
import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from miniagent.infrastructure.json_config import get_config
from miniagent.infrastructure.logger import get_logger
from miniagent.infrastructure.trace_events import (
    EVENT_ERROR_COLLECT,
    EVENT_LLM_REQUEST,
    EVENT_LLM_RESPONSE,
    EVENT_TOOL_END,
    EVENT_TOOL_START,
    EVENT_TOOL_ERROR,
)

_logger = get_logger(__name__)


def get_trace_output_dir() -> Path:
    """获取 Trace 输出目录。

    优先级：
    1. 配置 trace.output_dir
    2. 环境变量 MINIAGENT_TRACE_LOG_FILE 的目录
    3. 默认 workspaces/logs
    """
    config_dir = get_config("trace.output_dir", None)
    if config_dir:
        return Path(config_dir)

    env_file = os.environ.get("MINIAGENT_TRACE_LOG_FILE", "").strip()
    if env_file:
        return Path(env_file).parent

    return Path("workspaces/logs")


def get_trace_file(date: str | None = None) -> Path:
    """获取指定日期的 trace 文件路径。

    Args:
        date: 日期字符串（YYYY-MM-DD），默认今天

    Returns:
        Trace 文件路径（trace-YYYY-MM-DD.jsonl）
    """
    if date is None:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return get_trace_output_dir() / f"trace-{date}.jsonl"


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
    trace_file = get_trace_file(date)
    if not trace_file.exists():
        return []

    events = []
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
        return []

    return events


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
    # 按 tool_call_id 或 tool 名称配对 start/end
    tool_starts: dict[str, dict] = {}  # id -> start event
    tool_stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "total_ms": 0, "success": 0, "fail": 0}
    )

    for event in events:
        event_type = event.get("type", "")
        tool_name = event.get("tool", "")

        if event_type == EVENT_TOOL_START:
            # 记录开始时间
            tool_id = event.get("tool_call_id") or f"{tool_name}_{time.time_ns()}"
            tool_starts[tool_id] = event

        elif event_type == EVENT_TOOL_END:
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

    for event in events:
        event_type = event.get("type", "")

        if event_type == EVENT_LLM_REQUEST:
            request_count += 1
            total_messages += event.get("message_count", 0)
            total_tools += event.get("tool_count", 0)

        elif event_type == EVENT_LLM_RESPONSE:
            usage = event.get("usage", {})
            if usage:
                total_prompt_tokens += usage.get("prompt_tokens", 0)
                total_completion_tokens += usage.get("completion_tokens", 0)

    result = {
        "request_count": request_count,
        "total_tokens": {
            "prompt": total_prompt_tokens,
            "completion": total_completion_tokens,
        },
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

    # 摘要文本
    llm_count = report["llm"].get("request_count", 0)
    tool_count = sum(t.get("count", 0) for t in report["tools"].get("tools", {}).values())
    error_count = sum(e.get("count", 0) for e in report["errors"])

    report["summary"] = (
        f"LLM调用 {llm_count} 次，工具调用 {tool_count} 次，"
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

    cutoff_date = datetime.now(timezone.utc) - time.timedelta(days=retention_days)
    deleted = 0

    for trace_file in trace_dir.glob("trace-*.jsonl"):
        try:
            # 从文件名提取日期
            date_str = trace_file.stem.replace("trace-", "")
            file_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if file_date < cutoff_date:
                trace_file.unlink()
                deleted += 1
        except (ValueError, OSError):
            continue

    if deleted > 0:
        _logger.info("清理过期 trace 文件: %d 个", deleted)

    return deleted


__all__ = [
    "get_trace_output_dir",
    "get_trace_file",
    "load_trace_events",
    "compute_tool_stats",
    "compute_llm_stats",
    "compute_error_stats",
    "generate_daily_report",
    "save_report",
    "cleanup_old_traces",
]