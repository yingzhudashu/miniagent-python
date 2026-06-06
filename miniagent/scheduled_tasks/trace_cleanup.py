"""Trace文件定时清理任务。

提供定期清理过期trace文件的定时任务，防止磁盘占用过多。
可通过配置 trace.auto_cleanup 和 trace.retention_days 控制清理行为。

配置项：
- trace.auto_cleanup: 是否启用自动清理（默认 true）
- trace.retention_days: 保留天数（默认 7）
- trace.stats_report_interval: 报告生成间隔（秒，默认 3600）

用法：
1. 自动集成：shutdown 时自动清理一次
2. 定时清理：每小时执行一次清理任务
"""

from __future__ import annotations

import logging
import time
from typing import Any

from miniagent.infrastructure.json_config import get_config
from miniagent.infrastructure.trace_stats import (
    cleanup_old_traces,
    generate_daily_report,
    save_report,
)

_logger = logging.getLogger(__name__)


def scheduled_cleanup_traces() -> dict[str, Any]:
    """定时清理任务（每小时执行）。

    检查配置 trace.auto_cleanup，如果启用则清理过期trace文件。
    同时生成每日统计报告（如果启用）。

    Returns:
        {
          "success": True,
          "deleted_count": 5,
          "report_saved": True
        }
    """
    # 检查配置是否启用
    if not get_config("trace.auto_cleanup", True):
        return {"success": True, "reason": "auto_cleanup disabled"}

    # 清理过期trace文件
    retention_days = get_config("trace.retention_days", 7)
    deleted_count = cleanup_old_traces(retention_days)

    result = {
        "success": True,
        "deleted_count": deleted_count,
    }

    # 生成并保存每日报告（可选）
    if get_config("trace.enabled", False):
        try:
            report = generate_daily_report()
            if report.get("total_events", 0) > 0:
                save_report(report)
                result["report_saved"] = True
        except Exception as e:
            _logger.debug("scheduled_cleanup_traces: save_report failed: %s", e)

    return result


def scheduled_trace_stats_report() -> dict[str, Any]:
    """定时统计报告生成任务（每小时执行）。

    生成当日trace统计报告并保存到文件。
    与清理任务分开，可独立配置。

    Returns:
        {
          "success": True,
          "report_saved": True,
          "total_events": 1234
        }
    """
    # 检查trace是否启用
    if not get_config("trace.enabled", False):
        return {"success": True, "reason": "trace disabled"}

    try:
        report = generate_daily_report()
        if report.get("total_events", 0) > 0:
            save_report(report)
            return {
                "success": True,
                "report_saved": True,
                "total_events": report.get("total_events", 0),
            }
        else:
            return {"success": True, "reason": "no events today"}
    except Exception as e:
        _logger.error("scheduled_trace_stats_report failed: %s", e)
        return {"success": False, "error": str(e)}


_last_stats_report_at: float = 0.0


def maybe_scheduled_trace_stats_report() -> dict[str, Any] | None:
    """按 ``trace.stats_report_interval`` 节流生成统计报告（供定时 tick / shutdown 调用）。"""
    global _last_stats_report_at
    if not get_config("trace.enabled", False):
        return None
    interval = max(60.0, float(get_config("trace.stats_report_interval", 3600)))
    now = time.time()
    if _last_stats_report_at and (now - _last_stats_report_at) < interval:
        return None
    _last_stats_report_at = now
    return scheduled_trace_stats_report()


__all__ = [
    "scheduled_cleanup_traces",
    "scheduled_trace_stats_report",
    "maybe_scheduled_trace_stats_report",
]