"""Agent HTML 上传工具

提供 HTML 内容上传并获取可访问 URL 的能力，用于 Agent 结果在线展示。

特点：
- 上传 HTML 内容获取公开访问 URL
- API Key 认证机制
- 自动安全清理（防止 XSS）
- 文件大小限制（2MB）
- 支持文件列表和清理操作

工具列表：
- upload_html: 上传 HTML 内容，返回可访问 URL
- list_html_files: 列出已上传的 HTML 文件
- cleanup_html_files: 清理过期文件

使用示例：
    >>> result = await upload_html_handler({"html": "<html><body><h1>Hello</h1></body></html>"}, ctx)
    >>> print(result.content)  # https://robotclaw.site/agent-html/abc123

配置项（config.defaults.json）：
    - secrets.agent_html_api_key: API 密钥
    - agent_html.base_url: API 基础 URL
    - agent_html.max_size: 最大文件大小

设计背景见 docs/ARCHITECTURE.md § 工具层。
"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

import aiohttp

from miniagent.agent.logging import get_logger
from miniagent.agent.types.error_prefix import ERROR_PREFIX, SUCCESS_PREFIX
from miniagent.agent.types.tool import ToolContext, ToolDefinition, ToolResult
from miniagent.assistant.infrastructure.json_config import get_config

_logger = get_logger(__name__)

# ─── 配置常量 ───

DEFAULT_BASE_URL = "https://robotclaw.site"
DEFAULT_MAX_SIZE = 2 * 1024 * 1024  # 2MB
DEFAULT_TIMEOUT = 30  # 秒

_http_sessions: dict[asyncio.AbstractEventLoop, aiohttp.ClientSession] = {}
_http_sessions_lock = threading.Lock()


def _get_http_session() -> aiohttp.ClientSession:
    """Return a reusable connection pool owned by the current event loop."""
    loop = asyncio.get_running_loop()
    with _http_sessions_lock:
        session = _http_sessions.get(loop)
        if session is None or session.closed:
            session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT))
            _http_sessions[loop] = session
        return session


async def close_html_upload_http_clients() -> None:
    """Close every loop-scoped HTML upload connection pool."""
    with _http_sessions_lock:
        sessions = tuple(_http_sessions.values())
        _http_sessions.clear()
    if sessions:
        await asyncio.gather(
            *(session.close() for session in sessions),
            return_exceptions=True,
        )


def _get_api_key() -> str | None:
    """获取 API Key（从 secrets 配置）"""
    return get_config("secrets.agent_html_api_key", None)


def _get_base_url() -> str:
    """获取 API 基础 URL"""
    return get_config("agent_html.base_url", DEFAULT_BASE_URL)


def _get_max_size() -> int:
    """获取最大文件大小限制"""
    return get_config("agent_html.max_size", DEFAULT_MAX_SIZE)


# ─── 工具 Handler ───


async def _upload_html_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """上传 HTML 内容并获取可访问 URL

    Args (via args):
        html: HTML 内容字符串（必填）
        filename: 自定义文件名（可选，仅作参考）

    Returns:
        ToolResult with:
        - success: True/False
        - content: URL 或错误信息
        - meta: {id, filename, url, full_url}

    Example:
        >>> result = await _upload_html_handler({
        >>>     "html": "<html><body><h1>分析结果</h1></body></html>"
        >>> }, ctx)
        >>> if result.success:
        >>>     print(result.meta["full_url"])  # https://robotclaw.site/agent-html/abc123
    """
    html_content = args.get("html")
    if not html_content:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 缺少 html 参数")

    # 检查大小限制
    max_size = _get_max_size()
    content_size = len(html_content.encode("utf-8"))
    if content_size > max_size:
        return ToolResult(
            success=False, content=f"{ERROR_PREFIX} HTML 内容超过 {max_size // 1024 // 1024}MB 限制"
        )

    # 获取配置
    api_key = _get_api_key()
    if not api_key:
        return ToolResult(
            success=False,
            content=f"{ERROR_PREFIX} 未配置 agent_html_api_key，请在 config.user.json 的 secrets 部分设置",
        )

    base_url = _get_base_url()

    # 构建请求
    url = f"{base_url}/api/agent/html/upload"
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": api_key,
    }
    payload = {"html": html_content}

    # 可选文件名
    if "filename" in args:
        payload["filename"] = args["filename"]

    # 发送请求
    try:
        session = _get_http_session()
        async with session.post(url, headers=headers, json=payload) as response:
            result = await response.json()

            if response.status == 201 and result.get("success"):
                full_url = f"{base_url}{result['url']}"
                meta = {
                    "id": result.get("id"),
                    "filename": result.get("filename"),
                    "url": result.get("url"),
                    "full_url": full_url,
                }
                return ToolResult(
                    success=True,
                    content=f"{SUCCESS_PREFIX} HTML 已上传\n访问地址: {full_url}",
                    meta=meta,
                )

            # 处理错误
            error_code = result.get("error_code", "UNKNOWN")
            error_msg = result.get("error", "未知错误")

            error_messages = {
                "INVALID_API_KEY": "API Key 无效",
                "MISSING_HTML": "缺少 html 字段",
                "SIZE_EXCEEDED": "HTML 超过 2MB 限制",
                "SANITIZATION_FAILED": "HTML 包含危险内容（script、iframe、事件处理器等）",
                "SAVE_FAILED": "文件保存失败",
            }

            friendly_msg = error_messages.get(error_code, error_msg)
            return ToolResult(
                success=False, content=f"{ERROR_PREFIX} 上传失败: {friendly_msg} ({error_code})"
            )

    except aiohttp.ClientError as e:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 网络请求失败: {e}")
    except json.JSONDecodeError as e:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 响应解析失败: {e}")
    except Exception as e:
        _logger.exception("upload_html 异常")
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 上传异常: {e}")


async def _list_html_files_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """列出已上传的 HTML 文件

    Returns:
        ToolResult with:
        - success: True/False
        - content: 文件列表信息
        - meta: {files: [...], count: N}

    Example:
        >>> result = await _list_html_files_handler({}, ctx)
        >>> for f in result.meta["files"]:
        >>>     print(f["url"])
    """
    # 获取配置
    api_key = _get_api_key()
    if not api_key:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 未配置 agent_html_api_key")

    base_url = _get_base_url()
    url = f"{base_url}/api/agent/html/list"
    headers = {"X-API-Key": api_key}

    try:
        session = _get_http_session()
        async with session.get(url, headers=headers) as response:
            result = await response.json()

            if response.status == 200:
                files = result.get("files", [])
                count = result.get("count", 0)

                if count == 0:
                    return ToolResult(
                        success=True,
                        content=f"{SUCCESS_PREFIX} 无已上传的 HTML 文件",
                        meta={"files": [], "count": 0},
                    )

                # 格式化文件列表
                lines = [f"{SUCCESS_PREFIX} 已上传 {count} 个 HTML 文件:\n"]
                for f in files:
                    full_url = f"{base_url}{f['url']}"
                    lines.append(f"  - {f['filename']} -> {full_url}")

                return ToolResult(
                    success=True, content="\n".join(lines), meta={"files": files, "count": count}
                )

            error_msg = result.get("error", "未知错误")
            return ToolResult(success=False, content=f"{ERROR_PREFIX} 获取列表失败: {error_msg}")

    except aiohttp.ClientError as e:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 网络请求失败: {e}")
    except Exception as e:
        _logger.exception("list_html_files 异常")
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 异常: {e}")


async def _cleanup_html_files_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """清理过期的 HTML 文件

    Args (via args):
        days: 删除超过 N 天的文件（默认 7）

    Returns:
        ToolResult with:
        - success: True/False
        - content: 清理结果
        - meta: {deleted_count: N, deleted_files: [...]}

    Example:
        >>> result = await _cleanup_html_files_handler({"days": 7}, ctx)
        >>> print(f"清理了 {result.meta['deleted_count']} 个文件")
    """
    days = args.get("days", 7)

    # 获取配置
    api_key = _get_api_key()
    if not api_key:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 未配置 agent_html_api_key")

    base_url = _get_base_url()
    url = f"{base_url}/api/agent/html/cleanup"
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": api_key,
    }
    payload = {"days": days}

    try:
        session = _get_http_session()
        async with session.post(url, headers=headers, json=payload) as response:
            result = await response.json()

            if response.status == 200 and result.get("success"):
                deleted_count = result.get("deleted_count", 0)
                deleted_files = result.get("deleted_files", [])

                if deleted_count == 0:
                    return ToolResult(
                        success=True,
                        content=f"{SUCCESS_PREFIX} 无需清理，没有超过 {days} 天的文件",
                        meta={"deleted_count": 0, "deleted_files": []},
                    )

                return ToolResult(
                    success=True,
                    content=f"{SUCCESS_PREFIX} 已清理 {deleted_count} 个过期文件（超过 {days} 天）",
                    meta={"deleted_count": deleted_count, "deleted_files": deleted_files},
                )

            error_msg = result.get("error", "未知错误")
            return ToolResult(success=False, content=f"{ERROR_PREFIX} 清理失败: {error_msg}")

    except aiohttp.ClientError as e:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 网络请求失败: {e}")
    except Exception as e:
        _logger.exception("cleanup_html_files 异常")
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 异常: {e}")


# ─── 工具定义 ───

from miniagent.assistant.tools.base import tool

# upload_html 工具
upload_html_tool: ToolDefinition = (
    tool("upload_html", "上传 HTML 内容并获取可访问 URL，用于在线文档/演示展示")
    .param("html", "string", "HTML 内容字符串", required=True)
    .param("filename", "string", "自定义文件名（可选，仅作参考）")
    .toolbox("html_upload")
    .handler(_upload_html_handler)
    .build()
)

# list_html_files 工具
list_html_files_tool: ToolDefinition = (
    tool("list_html_files", "列出已上传的 HTML 文件")
    .toolbox("html_upload")
    .handler(_list_html_files_handler)
    .build()
)

# cleanup_html_files 工具
cleanup_html_files_tool: ToolDefinition = (
    tool("cleanup_html_files", "清理过期的 HTML 文件")
    .param("days", "number", "删除超过 N 天的文件（默认 7）")
    .toolbox("html_upload")
    .handler(_cleanup_html_files_handler)
    .build()
)


__all__ = [
    "upload_html_tool",
    "list_html_files_tool",
    "cleanup_html_files_tool",
    "_upload_html_handler",
    "_list_html_files_handler",
    "_cleanup_html_files_handler",
    "close_html_upload_http_clients",
]
