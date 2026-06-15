"""运行日志分析器。

从 trace.jsonl、activity_log、错误统计中提取运行指标，
识别性能瓶颈、高频错误、异常行为模式，
生成结构化运行分析报告。

分析维度：
1. 工具调用统计：成功率、平均时延、失败分布
2. LLM 调用统计：请求次数、token 消耗、响应时延
3. 错误汇总：按类型/工具分组、用户误用 vs 工具缺陷
4. 循环检测：重复调用、ping-pong 模式
5. 上下文行为：压缩频率、token 估算

输出报告格式：
{
  "date": "2026-06-05",
  "tools": {
    "stats": { "read_file": { "count": 10, "avg_ms": 50, "success_rate": 1.0 } },
    "slow_tools": [...],
    "failed_tools": [...]
  },
  "llm": { "request_count": 10, "total_tokens": { ... } },
  "errors": [...],
  "loops": [...],
  "context": { "compress_count": 5, "avg_tokens": 5000 }
}

使用方式：
    analyzer = RuntimeAnalyzer()
    report = analyzer.analyze_today()
    analyzer.save_report(report)

详见 docs/SELF_OPT.md（运行日志驱动提案）。
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from miniagent.infrastructure.logger import get_logger
from miniagent.infrastructure.trace_events import EVENT_TOOL_END
from miniagent.infrastructure.trace_stats import (
    compute_context_stats,
    compute_error_stats,
    compute_llm_stats,
    compute_tool_stats,
    load_trace_events,
)

_logger = get_logger(__name__)


def get_activity_log_dir() -> Path:
    """获取活动日志目录。

    默认：workspaces/memory
    """
    from miniagent.infrastructure.paths import resolve_state_dir

    return Path(resolve_state_dir()) / "memory"


def get_activity_log_file(date: str | None = None) -> Path:
    """获取指定日期的活动日志文件。

    Args:
        date: 日期字符串（YYYY-MM-DD），默认今天

    Returns:
        活动日志文件路径（{YYYY-MM-DD}.md）
    """
    if date is None:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return get_activity_log_dir() / f"{date}.md"


def parse_activity_log(date: str | None = None) -> dict[str, Any]:
    """解析 Markdown 格式的活动日志。

    提取：
    - 工具调用详情（tool name、duration、success、error_type）
    - LLM 调用详情（model、tokens、thinking）
    - 会话列表

    Args:
        date: 日期字符串，默认今天

    Returns:
        解析结果：
        {
          "sessions": ["cli-1", "feishu-oc_xxx"],
          "tool_calls": [
            { "tool": "read_file", "duration_ms": 150, "success": true, "session": "cli-1" },
            ...
          ],
          "llm_calls": [
            { "model": "gpt-4o-mini", "prompt_tokens": 1000, "session": "cli-1" },
            ...
          ],
          "errors": [
            { "error_type": "TimeoutError", "tool": "web_search", "session": "cli-1" },
            ...
          ]
        }
    """
    log_file = get_activity_log_file(date)
    if not log_file.exists():
        return {"sessions": [], "tool_calls": [], "llm_calls": [], "errors": []}

    result = {
        "sessions": [],
        "tool_calls": [],
        "llm_calls": [],
        "errors": [],
    }

    try:
        content = log_file.read_text(encoding="utf-8")

        # 提取会话标识
        session_pattern = re.compile(r"##\s+([^\s(]+)")
        for match in session_pattern.finditer(content):
            session_key = match.group(1)
            if session_key not in result["sessions"]:
                result["sessions"].append(session_key)

        # 提取工具调用
        tool_pattern = re.compile(
            r"###\s+工具调用:\s+(\S+)\s+\[(ok|fail)\].*?"
            r"-\s+duration:\s+(\d+)ms",
            re.DOTALL,
        )
        for match in tool_pattern.finditer(content):
            tool_name = match.group(1)
            status = match.group(2)
            duration_ms = int(match.group(3))
            result["tool_calls"].append({
                "tool": tool_name,
                "duration_ms": duration_ms,
                "success": status == "ok",
            })

        # 提取错误类型
        error_pattern = re.compile(r"-\s+error_type:\s+(\S+)")
        for match in error_pattern.finditer(content):
            error_type = match.group(1)
            result["errors"].append({"error_type": error_type})

        # 提取 LLM 调用
        llm_pattern = re.compile(
            r"###\s+LLM\s+调用.*?"
            r"-\s+model:\s+(\S+).*?"
            r"-\s+tokens:\s+prompt=(\d+),\s+completion=(\d+)",
            re.DOTALL,
        )
        for match in llm_pattern.finditer(content):
            model = match.group(1)
            prompt_tokens = int(match.group(2))
            completion_tokens = int(match.group(3))
            result["llm_calls"].append({
                "model": model,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
            })

    except OSError as e:
        _logger.warning("解析活动日志失败: %s", e)

    return result


def _detect_loop_patterns(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """从 trace 事件检测重复调用与 ping-pong 模式。"""
    loops: list[dict[str, Any]] = []
    by_session: dict[str, list[str]] = defaultdict(list)

    for event in events:
        if event.get("type") == EVENT_TOOL_END:
            session = event.get("session_key", "unknown")
            tool = event.get("tool", "")
            if tool:
                by_session[session].append(tool)

    repeat_threshold = 5
    for session, tools in by_session.items():
        for tool, count in Counter(tools).items():
            if count >= repeat_threshold:
                loops.append({
                    "type": "repeated_tool",
                    "session": session,
                    "tool": tool,
                    "count": count,
                    "severity": 2,
                })

        if len(tools) >= 6:
            a, b = tools[0], tools[1]
            if a != b:
                is_ping_pong = all(
                    t == (a if i % 2 == 0 else b) for i, t in enumerate(tools[:6])
                )
                if is_ping_pong:
                    loops.append({
                        "type": "ping_pong",
                        "session": session,
                        "tools": [a, b],
                        "severity": 3,
                    })

    return loops


class RuntimeAnalyzer:
    """运行日志分析器。

    从 trace 和 activity_log 提取运行指标，
    生成结构化分析报告。
    """

    def __init__(self) -> None:
        """初始化分析器。"""
        pass

    def analyze(self, date: str | None = None) -> dict[str, Any]:
        """执行完整分析。

        Args:
            date: 日期字符串，默认今天

        Returns:
            运行分析报告
        """
        if date is None:
            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        report = {
            "date": date,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

        # 1. Trace 统计
        trace_events = load_trace_events(date)
        report["trace_events_count"] = len(trace_events)

        # 工具统计
        tool_stats = compute_tool_stats(trace_events)
        report["tools"] = tool_stats

        # LLM 统计
        llm_stats = compute_llm_stats(trace_events)
        report["llm"] = llm_stats

        # 错误统计
        error_stats = compute_error_stats(trace_events)
        report["errors"] = error_stats

        # 上下文压缩统计
        context_stats = compute_context_stats(trace_events)
        report["context"] = context_stats

        # 循环检测
        loop_patterns = _detect_loop_patterns(trace_events)
        report["loops"] = loop_patterns

        # 2. 活动日志分析
        activity_data = parse_activity_log(date)
        report["sessions_count"] = len(activity_data["sessions"])
        report["sessions"] = activity_data["sessions"]

        # 合并工具调用数据
        if activity_data["tool_calls"]:
            report["activity_tool_calls"] = len(activity_data["tool_calls"])

        # 合并错误数据
        if activity_data["errors"]:
            report["activity_errors"] = len(activity_data["errors"])

        # 3. 生成摘要
        llm_count = llm_stats.get("request_count", 0)
        tool_count = sum(
            t.get("count", 0) for t in tool_stats.get("tools", {}).values()
        )
        error_count = sum(e.get("count", 0) for e in error_stats)
        session_count = len(activity_data["sessions"])
        compress_count = context_stats.get("compress_count", 0)
        loop_count = len(loop_patterns)

        report["summary"] = (
            f"会话 {session_count} 个，"
            f"LLM 调用 {llm_count} 次，"
            f"工具调用 {tool_count} 次，"
            f"上下文压缩 {compress_count} 次，"
            f"循环模式 {loop_count} 个，"
            f"错误 {error_count} 次"
        )

        # 4. 问题标记
        issues = []

        # 慢工具问题
        for tool in tool_stats.get("slow_tools", []):
            issues.append({
                "type": "slow_tool",
                "tool": tool["name"],
                "avg_ms": tool["avg_ms"],
                "severity": 2,
            })

        # 高失败率工具问题
        for tool in tool_stats.get("failed_tools", []):
            issues.append({
                "type": "tool_failure",
                "tool": tool["name"],
                "success_rate": tool["success_rate"],
                "severity": 3,
            })

        # 高频错误问题
        for error in error_stats[:5]:  # 前 5 个高频错误
            if error.get("count", 0) >= 3:
                issues.append({
                    "type": "high_frequency_error",
                    "error_type": error["type"],
                    "count": error["count"],
                    "is_user_error": error.get("is_user_error", False),
                    "severity": 2 if error.get("is_user_error") else 3,
                })

        # 循环模式问题
        for loop in loop_patterns:
            if loop.get("type") == "repeated_tool":
                issues.append({
                    "type": "tool_loop",
                    "tool": loop["tool"],
                    "count": loop["count"],
                    "session": loop.get("session", ""),
                    "severity": loop.get("severity", 2),
                })
            elif loop.get("type") == "ping_pong":
                issues.append({
                    "type": "ping_pong",
                    "tools": loop.get("tools", []),
                    "session": loop.get("session", ""),
                    "severity": loop.get("severity", 3),
                })

        # 高频上下文压缩
        if context_stats.get("compress_count", 0) >= 5:
            issues.append({
                "type": "context_pressure",
                "compress_count": context_stats["compress_count"],
                "severity": 2,
            })

        report["issues"] = issues

        return report

    def save_report(self, report: dict[str, Any]) -> Path:
        """保存分析报告到文件。

        Args:
            report: 分析报告

        Returns:
            报告文件路径
        """
        from miniagent.core.self_opt.proposal_store import get_reports_dir

        reports_dir = get_reports_dir()
        date = report.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        report_file = reports_dir / f"runtime-{date}.json"

        with report_file.open("w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        _logger.info("运行分析报告已保存: %s", report_file)
        return report_file


__all__ = [
    "get_activity_log_dir",
    "get_activity_log_file",
    "parse_activity_log",
    "RuntimeAnalyzer",
]