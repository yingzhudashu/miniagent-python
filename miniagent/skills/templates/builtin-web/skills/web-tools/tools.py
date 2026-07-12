"""builtin-web skill tools — Web search, browser extraction, URL fetch.

从 ``miniagent.tools.web`` 提取的工具定义。注册到主工具注册表时
builtin 同名优先，因此本 skill 用于首次安装时提供 web 能力。
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any
from urllib.parse import urlparse

from miniagent.core.constants import (
    BROWSER_DISABLE_IMAGES,
    BROWSER_DISABLE_STYLES,
    BROWSER_TIMEOUT_SECONDS,
    WEB_SEARCH_TAVILY_TIMEOUT,
    WEB_SEARCH_TAVILY_URL,
)
from miniagent.infrastructure.browser_pool import (
    close_browser_pool,
)
from miniagent.infrastructure.browser_pool import (
    get_browser_instance as _get_browser_instance,
)
from miniagent.infrastructure.httpx_pool import get_shared_httpx_client
from miniagent.types.error_prefix import ERROR_PREFIX, SUCCESS_PREFIX
from miniagent.types.tool import ToolContext, ToolDefinition, ToolResult

_logger = logging.getLogger(__name__)


async def _cleanup_browser() -> None:
    """Compatibility hook delegated to the process-owned browser pool."""
    await close_browser_pool()


def _browser_resource_route_handler():
    """按 ``browser.disable_images`` / ``browser.disable_styles`` 构建 Playwright 路由拦截。"""
    disable_images = BROWSER_DISABLE_IMAGES
    disable_styles = BROWSER_DISABLE_STYLES
    exts: list[str] = []
    if disable_images:
        exts.extend(["png", "jpg", "jpeg", "gif", "svg"])
    if disable_styles:
        exts.extend(["css", "woff", "woff2"])
    if not exts:
        return None
    pattern = "**/*.{" + ",".join(exts) + "}"

    async def _handler(route: Any) -> None:
        await route.abort()

    return pattern, _handler


# ─── Tavily 配置 ────────────────────────────────────────────

_DEFAULT_TAVILY_URL = "https://api.tavily.com/search"


def _tavily_url() -> str:
    return WEB_SEARCH_TAVILY_URL


def _tavily_api_key() -> str:
    """获取 Tavily API Key（优先 TAVILY_API_KEY，fallback WEB_SEARCH_API_KEY）。"""
    # 敏感凭据，保留环境变量
    return (os.environ.get("TAVILY_API_KEY") or os.environ.get("WEB_SEARCH_API_KEY") or "").strip()


def _tavily_timeout_sec() -> float:
    """获取 Tavily 请求超时时间（秒）。"""
    return WEB_SEARCH_TAVILY_TIMEOUT


def _browser_timeout_ms() -> int:
    """获取浏览器工具超时时间（毫秒）。"""
    return int(float(BROWSER_TIMEOUT_SECONDS) * 1000)


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
            content=f"{ERROR_PREFIX} 未配置 TAVILY_API_KEY（或 WEB_SEARCH_API_KEY）。请在环境变量中设置 Tavily API Key。",
        )
    if not query:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} query 不能为空")

    payload = {
        "api_key": key,
        "query": query,
        "search_depth": "basic",
        "include_answer": True,
        "max_results": max_results,
    }
    timeout = _tavily_timeout_sec()

    try:
        client = await get_shared_httpx_client()
        resp = await client.post(
            _tavily_url(),
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=timeout,
            follow_redirects=False,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} Tavily 搜索失败: {e}")

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
        return ToolResult(
            success=False, content=f"{ERROR_PREFIX} 仅允许 http/https URL，且须包含主机名"
        )

    # 检查 Playwright 是否可用（不直接导入）
    import importlib.util

    if not importlib.util.find_spec("playwright"):
        return ToolResult(
            success=False,
            content=f"{ERROR_PREFIX} 未安装 Playwright。请执行：pip install miniagent-python[browser]\n然后：playwright install chromium",
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

            route_cfg = _browser_resource_route_handler()
            if route_cfg is not None:
                pattern, handler = route_cfg
                await page.route(pattern, handler)

            await page.goto(url, wait_until=wait_until, timeout=timeout_ms)
            text_out = (await page.inner_text("body")).strip()
        finally:
            # 关闭页面但不关闭浏览器（复用）
            await page.close()
    except Exception as e:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 浏览器抓取失败: {e}")

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
            client = await get_shared_httpx_client()
            resp = await client.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; MiniAgent/1.0)"},
                timeout=15.0,
                follow_redirects=True,
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
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 抓取失败: {e}")


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
                "filename": {
                    "type": "string",
                    "description": "保存的文件名（可选，默认从 URL 或 Content-Disposition 提取）",
                },
                "max_size_mb": {"type": "number", "description": "最大允许下载大小（MB，默认 50）"},
            },
            "required": ["url"],
        },
    },
}


def _open_binary_writer(path: str) -> Any:
    return open(path, "wb")


def _write_binary_file(path: str, data: bytes) -> None:
    with open(path, "wb") as file:
        file.write(data)


def _read_sync_http_response(response: Any) -> tuple[str, bytes]:
    try:
        content_type = response.headers.get("content-type", "application/octet-stream")
        return content_type, response.read()
    finally:
        response.close()


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
        return ToolResult(
            success=False, content=f"{ERROR_PREFIX} 仅允许 http/https URL，且须包含主机名"
        )

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
        client = await get_shared_httpx_client()
        # 先发送 HEAD 请求检查大小和类型
        try:
            head_resp = await client.head(url, timeout=timeout, follow_redirects=True)
            content_length = int(head_resp.headers.get("content-length", 0) or 0)
            content_type = head_resp.headers.get("content-type", "application/octet-stream")

            # 从 Content-Disposition 提取文件名（如果未指定）
            disposition = head_resp.headers.get("content-disposition", "")
            if not args.get("filename") and disposition:
                # 解析 filename="xxx" 或 filename*=UTF-8''xxx
                # parser already imported at module scope
                match = re.search(r'filename[*]?[="\']?([^"\';\s]+)["\']?', disposition)
                if match:
                    cd_name = unquote(match.group(1))
                    filename = os.path.basename(cd_name) or filename
                    save_path = os.path.join(save_dir, filename)

            # 检查大小限制
            if content_length > max_size_bytes:
                return ToolResult(
                    success=False,
                    content=f"{ERROR_PREFIX} 文件过大: {content_length / 1024 / 1024:.1f}MB > {max_size_mb}MB 限制",
                )
        except Exception:
            content_length = 0
            content_type = "application/octet-stream"

        # 流式下载
        total = 0
        try:
            async with client.stream("GET", url, timeout=timeout, follow_redirects=True) as resp:
                resp.raise_for_status()

                # 再次检查 Content-Type
                content_type = resp.headers.get("content-type", content_type)

                file = await asyncio.to_thread(_open_binary_writer, save_path)
                pending = bytearray()
                too_large = False
                try:
                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                        total += len(chunk)
                        if total > max_size_bytes:
                            too_large = True
                            break
                        pending.extend(chunk)
                        if len(pending) >= 1024 * 1024:
                            await asyncio.to_thread(file.write, bytes(pending))
                            pending.clear()
                    if pending and not too_large:
                        await asyncio.to_thread(file.write, bytes(pending))
                finally:
                    await asyncio.to_thread(file.close)
                if too_large:
                    await asyncio.to_thread(os.remove, save_path)
                    return ToolResult(
                        success=False,
                        content=f"{ERROR_PREFIX} 下载超过限制: {total / 1024 / 1024:.1f}MB > {max_size_mb}MB",
                    )
        except Exception as e:
            # 清理部分下载的文件
            if os.path.exists(save_path):
                try:
                    os.remove(save_path)
                except Exception as cleanup_error:
                    _logger.debug("清理下载文件失败: %s", cleanup_error)
            return ToolResult(success=False, content=f"{ERROR_PREFIX} 下载失败: {e}")

    except ImportError:
        # 无 httpx，使用 urllib 回退
        from urllib.request import urlopen

        try:
            resp_sync = await asyncio.to_thread(urlopen, url, timeout=timeout)
            content_type, data = await asyncio.to_thread(
                _read_sync_http_response,
                resp_sync,
            )
            if len(data) > max_size_bytes:
                return ToolResult(
                    success=False,
                    content=f"{ERROR_PREFIX} 文件过大: {len(data) / 1024 / 1024:.1f}MB > {max_size_mb}MB",
                )
            await asyncio.to_thread(_write_binary_file, save_path, data)
            total = len(data)
        except Exception as e:
            return ToolResult(success=False, content=f"{ERROR_PREFIX} 下载失败: {e}")

    # 格式化大小
    size_str = f"{total / 1024:.1f}KB" if total < 1024 * 1024 else f"{total / 1024 / 1024:.2f}MB"

    # 相对路径（便于用户理解）
    try:
        rel_path = os.path.relpath(save_path, save_dir)
    except ValueError:
        rel_path = filename

    return ToolResult(
        success=True,
        content=f"{SUCCESS_PREFIX} 下载完成\n文件: {rel_path}\n大小: {size_str}\n类型: {content_type}",
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
