"""Mini Agent Python — 网络工具 (Phase 5)

- web_search: Tavily 联网检索（标题、URL、摘要）
- browser_extract_text: Playwright 打开页面并抽取可见正文（CSR / 强 JS 站点）
- fetch_url: HTTP GET + HTML → 纯文本（轻量）
- get_time: 当前时间

Tavily / Playwright 可选依赖与 Key 见根目录 ``README``；HTTP 超时与环境变量见 ``.env.example``。
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from miniagent.types.tool import ToolContext, ToolDefinition, ToolResult

TAVILY_SEARCH_URL = "https://api.tavily.com/search"


def _tavily_api_key() -> str:
    """读取 Tavily Key（``TAVILY_API_KEY`` 或 ``WEB_SEARCH_API_KEY``）。"""
    return (os.environ.get("TAVILY_API_KEY") or os.environ.get("WEB_SEARCH_API_KEY") or "").strip()


def _tavily_timeout_sec() -> float:
    """Tavily HTTP 超时秒数（``TAVILY_TIMEOUT`` 或回退 ``AGENT_HTTP_TIMEOUT``）。"""
    raw = os.environ.get("TAVILY_TIMEOUT", "").strip()
    if raw:
        try:
            return max(5.0, float(raw))
        except ValueError:
            pass
    try:
        return max(5.0, float(os.environ.get("AGENT_HTTP_TIMEOUT", "120")))
    except ValueError:
        return 45.0


def _browser_timeout_ms() -> int:
    """Playwright 导航超时毫秒数（``BROWSER_TOOL_TIMEOUT`` 或 HTTP 超时推导）。"""
    raw = os.environ.get("BROWSER_TOOL_TIMEOUT", "").strip()
    if raw:
        try:
            return max(5000, int(float(raw) * 1000))
        except ValueError:
            pass
    try:
        sec = float(os.environ.get("AGENT_HTTP_TIMEOUT", "120"))
        return max(5000, int(sec * 1000))
    except ValueError:
        return 60000


def _allowed_http_url(url: str, *, https_only: bool = False) -> bool:
    """校验 URL 方案为主机名非空的 http(s)；可选强制 https。"""
    p = urlparse(url.strip())
    if p.scheme not in ("http", "https"):
        return False
    if https_only and p.scheme != "https":
        return False
    return bool(p.netloc)


# ════════════════════════════════════════════════════════
# web_search (Tavily)
# ════════════════════════════════════════════════════════

_web_search_schema = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "使用 Tavily 进行联网搜索，返回相关网页标题、链接与摘要。"
            "适用于天气、新闻、文档检索；需要渲染或登录的页面请再用 browser_extract_text。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索查询语句"},
                "maxResults": {"type": "number", "description": "最大结果条数（默认 8，最大 20）"},
            },
            "required": ["query"],
        },
    },
}


async def _web_search_handler(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
    """``web_search`` 工具实现：调用 Tavily 并格式化摘要结果。"""
    query = str(args["query"]).strip()
    max_results = min(20, max(1, int(args.get("maxResults", 8))))
    key = _tavily_api_key()
    if not key:
        return ToolResult(
            success=False,
            content="❌ 未配置 TAVILY_API_KEY（或 WEB_SEARCH_API_KEY）。请在环境变量中设置 Tavily API Key。",
        )
    if not query:
        return ToolResult(success=False, content="❌ query 不能为空")

    payload = {
        "api_key": key,
        "query": query,
        "search_depth": "basic",
        "include_answer": True,
        "max_results": max_results,
    }
    timeout = _tavily_timeout_sec()

    try:
        import httpx

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                TAVILY_SEARCH_URL,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        return ToolResult(success=False, content=f"❌ Tavily 搜索失败: {e}")

    lines: list[str] = [f"🔎 Tavily 搜索: {query}\n"]
    ans = data.get("answer")
    if isinstance(ans, str) and ans.strip():
        lines.append("简要答案:")
        lines.append(ans.strip())
        lines.append("")

    results = data.get("results")
    if isinstance(results, list) and results:
        lines.append("结果:")
        for i, item in enumerate(results[:max_results], 1):
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip() or "(无标题)"
            url = str(item.get("url") or "").strip()
            snippet = str(item.get("content") or item.get("snippet") or "").strip()
            lines.append(f"{i}. {title}")
            if url:
                lines.append(f"   URL: {url}")
            if snippet:
                clip = snippet[:1200] + ("…" if len(snippet) > 1200 else "")
                lines.append(f"   {clip}")
            lines.append("")
    else:
        lines.append("(无结构化结果，请尝试改写查询。)")

    return ToolResult(success=True, content="\n".join(lines).strip())


# ════════════════════════════════════════════════════════
# browser_extract_text (Playwright)
# ════════════════════════════════════════════════════════

_browser_extract_schema = {
    "type": "function",
    "function": {
        "name": "browser_extract_text",
        "description": (
            "使用无头 Chromium 打开网页并提取页面可见正文。"
            "适用于依赖前端渲染的站点；需先 pip install miniagent-python[browser] 并执行 playwright install chromium。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "http(s) 页面 URL"},
                "maxChars": {"type": "number", "description": "最大返回字符数（默认 12000）"},
                "waitUntil": {
                    "type": "string",
                    "description": "加载等待策略：load / domcontentloaded / networkidle（默认 domcontentloaded）",
                    "enum": ["load", "domcontentloaded", "networkidle"],
                },
            },
            "required": ["url"],
        },
    },
}


async def _browser_extract_handler(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
    """``browser_extract_text``：无头 Chromium 拉取页面 ``body`` 内可见文本。"""
    url = str(args["url"]).strip()
    max_chars = int(args.get("maxChars", 12000))
    wait_until = str(args.get("waitUntil", "domcontentloaded")).strip()
    if wait_until not in ("load", "domcontentloaded", "networkidle"):
        wait_until = "domcontentloaded"

    if not _allowed_http_url(url, https_only=False):
        return ToolResult(success=False, content="❌ 仅允许 http/https URL，且须包含主机名")

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return ToolResult(
            success=False,
            content=(
                "❌ 未安装 Playwright。请执行：pip install miniagent-python[browser]\n"
                "然后：playwright install chromium"
            ),
        )

    timeout_ms = _browser_timeout_ms()
    text_out = ""
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.goto(url, wait_until=wait_until, timeout=timeout_ms)
                text_out = (await page.inner_text("body")).strip()
            finally:
                await browser.close()
    except Exception as e:
        return ToolResult(success=False, content=f"❌ 浏览器抓取失败: {e}")

    text_out = re.sub(r"\n{3,}", "\n\n", text_out)
    if len(text_out) > max_chars:
        text_out = text_out[:max_chars] + "\n... (已截断，可调大 maxChars)"

    return ToolResult(success=True, content=text_out or "(空正文)")


# ════════════════════════════════════════════════════════
# fetch_url
# ════════════════════════════════════════════════════════

_fetch_url_schema = {
    "type": "function",
    "function": {
        "name": "fetch_url",
        "description": "抓取网页内容并提取可读文本（自动去除 HTML 标签和脚本）；静态页优先用本工具以节省成本",
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
            import asyncio
            from urllib.request import Request, urlopen

            req = Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; MiniAgent/1.0)"})
            resp_sync = await asyncio.to_thread(urlopen, req, timeout=15)
            text = resp_sync.read().decode("utf-8", errors="replace")

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
    "web_search": ToolDefinition(
        schema=_web_search_schema,
        handler=_web_search_handler,
        permission="sandbox",
        help_text="Tavily 联网搜索",
        toolbox="web",
    ),
    "browser_extract_text": ToolDefinition(
        schema=_browser_extract_schema,
        handler=_browser_extract_handler,
        permission="sandbox",
        help_text="无头浏览器提取页面正文",
        toolbox="web",
    ),
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
