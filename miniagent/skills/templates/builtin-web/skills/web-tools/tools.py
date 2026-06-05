"""builtin-web skill tools — Web search, browser extraction, URL fetch.

从 ``miniagent.tools.web`` 提取的工具定义。注册到主工具注册表时
builtin 同名优先，因此本 skill 用于首次安装时提供 web 能力。
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from typing import Any
from urllib.parse import urlparse

from miniagent.infrastructure.json_config import get_config
from miniagent.infrastructure.trace_events import (
    EVENT_BROWSER_CLOSE,
    EVENT_BROWSER_CREATE,
    EVENT_BROWSER_REUSE,
)
from miniagent.infrastructure.tracing import emit_trace
from miniagent.types.tool import ToolContext, ToolDefinition, ToolResult

_logger = logging.getLogger(__name__)

# ─── 全局浏览器实例池（性能优化）────────────────────────────
_global_browser: Any | None = None
_browser_lock = asyncio.Lock()
_browser_last_used: float = 0.0
_BROWSER_IDLE_TIMEOUT = get_config("browser.idle_timeout_seconds", 300.0)  # 5分钟无使用后自动关闭


async def _get_browser_instance() -> Any:
    """获取或创建全局浏览器实例（惰性初始化，连接池复用）。

    性能优化：
    - 单例模式避免重复启动 Chromium
    - 自动清理机制防止资源泄漏
    - 连接池复用减少启动延迟

    Returns:
        Playwright Browser 实例
    """
    global _global_browser, _browser_last_used

    async with _browser_lock:
        # 检查是否需要重新创建
        now = time.time()
        need_create = False

        if _global_browser is None:
            need_create = True
        elif (now - _browser_last_used) > _BROWSER_IDLE_TIMEOUT:
            # 清理旧实例（超时未使用）
            try:
                await _global_browser.close()
                emit_trace({
                    "type": EVENT_BROWSER_CLOSE,
                    "idle_seconds": int(now - _browser_last_used),
                })
                _logger.info("浏览器实例已清理（空闲超时）")
            except Exception as e:
                _logger.debug("关闭旧浏览器实例失败: %s", e)
            _global_browser = None
            need_create = True

        if need_create:
            # 创建新实例
            try:
                from playwright.async_api import async_playwright

                start_time = time.time()
                pw = async_playwright()

                # 兼容Mock测试：检测是否为上下文管理器
                if hasattr(pw, '__aenter__'):
                    # Mock环境：使用上下文管理器
                    p = await pw.__aenter__()
                else:
                    # 正常环境：使用start()方法
                    p = await pw.start()

                _global_browser = await p.chromium.launch(
                    headless=True,
                    # 性能优化参数
                    args=[
                        '--disable-blink-features=AutomationControlled',
                        '--disable-dev-shm-usage',  # 减少内存占用
                        '--no-sandbox',  # 提升启动速度
                        '--disable-setuid-sandbox',
                    ]
                )
                elapsed_ms = int((time.time() - start_time) * 1000)

                emit_trace({
                    "type": EVENT_BROWSER_CREATE,
                    "duration_ms": elapsed_ms,
                })
                _logger.info("全局浏览器实例已创建（复用模式），耗时 %dms", elapsed_ms)
            except Exception as e:
                _logger.error("创建浏览器实例失败: %s", e)
                _global_browser = None
                raise
        else:
            # 复用现有实例
            emit_trace({
                "type": EVENT_BROWSER_REUSE,
                "idle_seconds": int(now - _browser_last_used),
            })

        _browser_last_used = now
        return _global_browser


async def _cleanup_browser() -> None:
    """清理全局浏览器实例（进程退出时调用）。

    应在程序退出前调用，确保资源释放。
    """
    global _global_browser
    if _global_browser is not None:
        try:
            await _global_browser.close()
            emit_trace({"type": EVENT_BROWSER_CLOSE})
            _logger.info("全局浏览器实例已关闭")
        except Exception as e:
            _logger.debug("关闭浏览器失败: %s", e)
        _global_browser = None

# ─── Tavily 配置 ────────────────────────────────────────────

_TAVILY_URL = "https://api.tavily.com/search"


def _tavily_api_key() -> str:
    """获取 Tavily API Key（优先 TAVILY_API_KEY，fallback WEB_SEARCH_API_KEY）。"""
    # 敏感凭据，保留环境变量
    return (os.environ.get("TAVILY_API_KEY") or os.environ.get("WEB_SEARCH_API_KEY") or "").strip()


def _tavily_timeout_sec() -> float:
    """获取 Tavily 请求超时时间（秒）。"""
    return float(get_config("web_search.tavily_timeout", 45))


def _browser_timeout_ms() -> int:
    """获取浏览器工具超时时间（毫秒）。"""
    return int(float(get_config("web_search.browser_timeout", 60)) * 1000)


def _allowed_http_url(url: str, *, https_only: bool = False) -> bool:
    """验证 URL 是否为有效的 HTTP/HTTPS 链接。"""
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
                _TAVILY_URL,
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
    """使用无头 Chromium 打开网页并提取页面可见正文（性能优化：浏览器实例复用）。"""
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
            content="❌ 未安装 Playwright。请执行：pip install miniagent-python[browser]\n然后：playwright install chromium",
        )

    timeout_ms = _browser_timeout_ms()
    text_out = ""

    # 性能优化：使用全局浏览器实例池
    try:
        browser = await _get_browser_instance()
        try:
            page = await browser.new_page()
            # 性能优化：设置页面超时和资源加载策略
            page.set_default_timeout(timeout_ms)

            # 禁用不必要的资源加载（图片、样式、字体）以提升速度
            await page.route('**/*.{png,jpg,jpeg,gif,svg,css,woff,woff2}',
                            lambda route: route.abort())

            await page.goto(url, wait_until=wait_until, timeout=timeout_ms)
            text_out = (await page.inner_text("body")).strip()
        finally:
            # 关闭页面但不关闭浏览器（复用）
            await page.close()
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
# download_file
# ════════════════════════════════════════════════════════

_download_file_schema = {
    "type": "function",
    "function": {
        "name": "download_file",
        "description": (
            "下载 HTTP 文件到会话沙箱目录。"
            "适用于下载 PDF、ZIP、图片、视频等二进制文件；返回文件路径和大小。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "HTTP/HTTPS 文件 URL"},
                "filename": {"type": "string", "description": "保存的文件名（可选，默认从 URL 或 Content-Disposition 提取）"},
                "max_size_mb": {"type": "number", "description": "最大允许下载大小（MB，默认 50）"},
            },
            "required": ["url"],
        },
    },
}


async def _download_file_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """下载 HTTP 文件到沙箱目录。

    Args:
        url: HTTP/HTTPS 文件 URL
        filename: 保存的文件名（可选）
        max_size_mb: 最大下载大小限制（MB）

    Returns:
        ToolResult 包含文件路径、大小、MIME 类型等信息
    """
    import os
    from urllib.parse import unquote, urlparse

    url = str(args["url"]).strip()
    max_size_mb = min(500, max(1, int(args.get("max_size_mb", 50))))
    max_size_bytes = max_size_mb * 1024 * 1024

    if not _allowed_http_url(url, https_only=False):
        return ToolResult(success=False, content="❌ 仅允许 http/https URL，且须包含主机名")

    # 解析默认文件名
    parsed = urlparse(url)
    default_name = unquote(os.path.basename(parsed.path) or "downloaded_file")

    # 用户指定的文件名，或从 URL/Content-Disposition 提取
    filename = str(args.get("filename", "")).strip() or default_name

    # 安全：禁止路径穿越
    filename = os.path.basename(filename)
    if not filename:
        filename = "downloaded_file"

    # 沙箱目录：使用 ctx.cwd（会话 files 目录）
    save_dir = ctx.cwd
    os.makedirs(save_dir, exist_ok=True)
    save_path = os.path.join(save_dir, filename)

    timeout = 120.0
    try:
        import httpx

        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            # 先发送 HEAD 请求检查大小和类型
            try:
                head_resp = await client.head(url)
                content_length = int(head_resp.headers.get("content-length", 0) or 0)
                content_type = head_resp.headers.get("content-type", "application/octet-stream")

                # 从 Content-Disposition 提取文件名（如果未指定）
                disposition = head_resp.headers.get("content-disposition", "")
                if not args.get("filename") and disposition:
                    # 解析 filename="xxx" 或 filename*=UTF-8''xxx
                    import re
                    match = re.search(r'filename[*]?=["\']?([^"\';\s]+)["\']?', disposition)
                    if match:
                        cd_name = unquote(match.group(1))
                        filename = os.path.basename(cd_name) or filename
                        save_path = os.path.join(save_dir, filename)

                # 检查大小限制
                if content_length > max_size_bytes:
                    return ToolResult(
                        success=False,
                        content=f"❌ 文件过大: {content_length / 1024 / 1024:.1f}MB > {max_size_mb}MB 限制",
                    )
            except Exception:
                content_length = 0
                content_type = "application/octet-stream"

            # 流式下载
            total = 0
            try:
                async with client.stream("GET", url) as resp:
                    resp.raise_for_status()

                    # 再次检查 Content-Type
                    content_type = resp.headers.get("content-type", content_type)

                    with open(save_path, "wb") as f:
                        for chunk in resp.iter_bytes(chunk_size=8192):
                            total += len(chunk)
                            if total > max_size_bytes:
                                f.close()
                                os.remove(save_path)
                                return ToolResult(
                                    success=False,
                                    content=f"❌ 下载超过限制: {total / 1024 / 1024:.1f}MB > {max_size_mb}MB",
                                )
                            f.write(chunk)
            except Exception as e:
                # 清理部分下载的文件
                if os.path.exists(save_path):
                    try:
                        os.remove(save_path)
                    except Exception as e:
                        _logger.debug("清理下载文件失败: %s", e)
                return ToolResult(success=False, content=f"❌ 下载失败: {e}")

    except ImportError:
        # 无 httpx，使用 urllib 回退
        import asyncio
        from urllib.request import urlopen

        try:
            resp_sync = await asyncio.to_thread(urlopen, url, timeout=timeout)
            content_type = resp_sync.headers.get("content-type", "application/octet-stream")
            data = resp_sync.read()
            if len(data) > max_size_bytes:
                return ToolResult(
                    success=False,
                    content=f"❌ 文件过大: {len(data) / 1024 / 1024:.1f}MB > {max_size_mb}MB",
                )
            with open(save_path, "wb") as f:
                f.write(data)
            total = len(data)
        except Exception as e:
            return ToolResult(success=False, content=f"❌ 下载失败: {e}")

    # 格式化大小
    size_str = f"{total / 1024:.1f}KB" if total < 1024 * 1024 else f"{total / 1024 / 1024:.2f}MB"

    # 相对路径（便于用户理解）
    try:
        rel_path = os.path.relpath(save_path, save_dir)
    except ValueError:
        rel_path = filename

    return ToolResult(
        success=True,
        content=f"✅ 下载完成\n文件: {rel_path}\n大小: {size_str}\n类型: {content_type}",
        meta={
            "path": save_path,
            "filename": filename,
            "size": total,
            "content_type": content_type,
        },
    )


# ─── ToolDefinition 注册 ──────────────────────────────────

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
    "download_file": ToolDefinition(
        schema=_download_file_schema,
        handler=_download_file_handler,
        permission="sandbox",
        help_text="下载 HTTP 文件到沙箱目录",
        toolbox="web",
    ),
}
