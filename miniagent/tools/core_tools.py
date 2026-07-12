"""Mini Agent Python — 核心工具

提供基础核心功能：
- get_time: 返回指定时区的当前时间和日期信息

重构说明：
- 从 web.py 重命名而来（原命名误导，实际无 Web 功能）
- check_app_availability 已合并到 skills.py（技能管理范畴）
- 使用 ToolBuilder 简化工具定义
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any

from miniagent.infrastructure.timezone_config import process_timezone
from miniagent.tools.base import tool
from miniagent.types.tool import ToolContext, ToolDefinition, ToolResult


async def _time_handler(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
    """获取指定时区当前本地时间与 UTC 偏移说明。"""
    tz_name = str(args.get("timezone", "")).strip() or process_timezone()
    tz: tzinfo

    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(tz_name)
        now = datetime.now(tz)
    except (ImportError, KeyError):
        if tz_name == "Asia/Shanghai":
            tz = timezone(timedelta(hours=8))
            now = datetime.now(tz)
        else:
            now = datetime.now(timezone.utc)
            tz_name = "UTC"

    weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    weekday = weekdays[now.weekday()]
    formatted = f"{now.year}年{now.month}月{now.day}日{weekday} {now.strftime('%H:%M:%S')}"
    iso = now.isoformat()

    return ToolResult(success=True, content=f"当前时间 ({tz_name}): {formatted}\nISO: {iso}")


# ════════════════════════════════════════════════════════
# Tool Definition (使用 ToolBuilder)
# ════════════════════════════════════════════════════════

core_tools: dict[str, ToolDefinition] = {
    "get_time": tool("get_time", "获取当前时间和日期信息")
        .optional("timezone", "string", "时区名称（如 Asia/Shanghai），默认使用系统时区")
        .sandbox()
        .toolbox("core")
        .handler(_time_handler)
        .build(),
}

__all__ = ["core_tools"]
