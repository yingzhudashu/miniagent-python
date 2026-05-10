"""Mini Agent Python — 网络工具 (Phase 5)

提供 2 个网络工具：
- fetch_url: 抓取网页内容（HTML → 纯文本）
- get_time: 获取当前时间
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone, timedelta
from typing import Any

from miniagent.types.tool import ToolDefinition, ToolContext, ToolResult

# ════════════════════════════════════════════════════════
# fetch_url
# ════════════════════════════════════════════════════════

_fetch_url_schema = {
    "type": "function",
    "function": {
        "name": "fetch_url",
        "description": "抓取网页内容并提取可读文本（自动去除 HTML 标签和脚本）",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "要抓取的 HTTP/HTTPS 网址"},
                "maxChars": {"type": "number", "description": "最大返回字符数（默认 5000）"},
            },
            "required": ["url"],
        },
    },
}


async def _fetch_url_handler(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
    """fetch_url 处理器。使用 httpx 异步请求（如果可用），否则回退到 urllib。"""
    url = str(args["url"])
    max_chars = int(args.get("maxChars", 5000))

    try:
        # 尝试使用 httpx（推荐的异步 HTTP 客户端）
        try:
            import httpx

            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                resp = await client.get(
                    url,
                    headers={"User-Agent": "Mozilla/5.0 (compatible; MiniAgent/1.0)"},
                )
                resp.raise_for_status()
                text = resp.text
        except ImportError:
            # 回退到 urllib（同步，但在 asyncio 中使用 to_thread）
            import asyncio
            from urllib.request import Request, urlopen

            req = Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; MiniAgent/1.0)"})
            resp_sync = await asyncio.to_thread(urlopen, req, timeout=15)
            text = resp_sync.read().decode("utf-8", errors="replace")

        # HTML → 纯文本
        clean = re.sub(r"<script[^>]*>[\s\S]*?</script>", "", text, flags=re.IGNORECASE)
        clean = re.sub(r"<style[^>]*>[\s\S]*?</style>", "", clean, flags=re.IGNORECASE)
        clean = re.sub(r"<[^>]+>", "\n", clean)
        clean = re.sub(r"\n{3,}", "\n\n", clean).strip()

        if len(clean) > max_chars:
            clean = clean[:max_chars] + "\n... (已截断，使用 maxChars 参数获取更多)"

        return ToolResult(success=True, content=clean)

    except Exception as e:
        return ToolResult(success=False, content=f"❌ 抓取失败: {e}")


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
                "timezone": {"type": "string", "description": "时区名称（如 Asia/Shanghai），默认使用系统时区"},
            },
        },
    },
}


async def _time_handler(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
    """get_time 处理器。"""
    tz_name = str(args.get("timezone", "")) or os.environ.get("TZ", "Asia/Shanghai")

    # 尝试使用 zoneinfo（Python 3.9+）
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(tz_name)
        now = datetime.now(tz)
    except (ImportError, KeyError):
        # 回退到 UTC + 手动偏移（仅支持 Asia/Shanghai）
        if tz_name == "Asia/Shanghai":
            tz = timezone(timedelta(hours=8))
            now = datetime.now(tz)
        else:
            now = datetime.now(timezone.utc)
            tz_name = "UTC"

    # 格式化
    weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    weekday = weekdays[now.weekday()]
    formatted = f"{now.year}年{now.month}月{now.day}日{weekday} {now.strftime('%H:%M:%S')}"
    iso = now.isoformat()

    return ToolResult(success=True, content=f"当前时间 ({tz_name}): {formatted}\nISO: {iso}")


# ─── 导出 ────────────────────────────────────────────────

web_tools: dict[str, ToolDefinition] = {
    "fetch_url": ToolDefinition(
        schema=_fetch_url_schema,
        handler=_fetch_url_handler,
        permission="sandbox",
        help_text="抓取网页内容并提取文本",
        toolbox="web",
    ),
    "get_time": ToolDefinition(
        schema=_time_schema,
        handler=_time_handler,
        permission="sandbox",
        help_text="获取当前时间和日期",
        toolbox="core",
    ),
}

__all__ = ["web_tools"]
