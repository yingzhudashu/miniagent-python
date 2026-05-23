"""Mini Agent Python — 时间工具

get_time: 返回指定时区的当前时间和日期信息。

web_search、browser_extract_text、fetch_url 已移至
``miniagent/skills/templates/builtin-web`` skill 模板，启动时作为 skill 注册。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from miniagent.types.tool import ToolContext, ToolDefinition, ToolResult

# ════════════════════════════════════════════════════════
# get_time
# ════════════════════════════════════════════════════════

_time_schema = {
    "type": "function",
    "function": {
        "name": "get_time",
        "description": "获取当前时间和日期信息",
        "parameters": {
            "type": "object",
            "properties": {
                "timezone": {
                    "type": "string",
                    "description": "时区名称（如 Asia/Shanghai），默认使用系统时区",
                },
            },
        },
    },
}


async def _time_handler(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
    """``get_time``：返回指定时区当前本地时间与 UTC 偏移说明。"""
    from miniagent.infrastructure.timezone_config import process_timezone

    tz_name = str(args.get("timezone", "")).strip() or process_timezone()

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


web_tools: dict[str, ToolDefinition] = {
    "get_time": ToolDefinition(
        schema=_time_schema,
        handler=_time_handler,
        permission="sandbox",
        help_text="获取当前时间和日期",
        toolbox="core",
    ),
}

__all__ = ["web_tools"]
