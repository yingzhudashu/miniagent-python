"""飞书云盘：drive v1 列举、根文件夹元数据（HTTP）、及 ``lark-oapi`` 列举封装。

HTTP 调用使用 httpx 异步客户端，避免阻塞事件循环。
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from miniagent.feishu.lark_client import build_client, clear_client_cache
from miniagent.feishu.lark_response import format_lark_response_error
from miniagent.feishu.types import FeishuConfig

# 单次列举上限（与工具层一致）
LIST_FILE_PAGE_SIZE = 50

_TENANT_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
_ROOT_FOLDER_META_URL = "https://open.feishu.cn/open-apis/drive/explorer/v2/root_folder/meta"

# ─── Token 缓存（性能优化）──

# Tenant Access Token 缓存（带 TTL，1.5 小时有效期）
_token_cache: dict[str, tuple[str, float]] = {}  # app_id -> (token, expiry_timestamp)
_TOKEN_TTL_SECONDS = 5400  # 1.5 小时（飞书 token 有效期 2 小时）

# httpx 客户端缓存（复用连接池）
_http_client: httpx.AsyncClient | None = None


def _get_http_client() -> httpx.AsyncClient:
    """获取或创建全局 httpx 客户端（复用连接池）。"""
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=30.0)
    return _http_client


def reset_http_client() -> None:
    """重置 httpx 客户端（测试用）。"""
    global _http_client
    if _http_client is not None:
        # 注意：不在此处 close，由调用方在适当时机关闭
        _http_client = None


def _get_cached_tenant_token(config: FeishuConfig) -> str:
    """获取缓存的 tenant_access_token（带 TTL）。

    注意：首次获取时仍使用同步 HTTP（用于快速启动场景），
    后续刷新在异步上下文中调用 async 版本。
    """
    key = config.app_id
    now = time.time()
    cached = _token_cache.get(key)
    if cached and cached[1] > now:
        return cached[0]
    # 未缓存或已过期，重新获取（同步）
    token = _fetch_tenant_access_token_sync(config)
    _token_cache[key] = (token, now + _TOKEN_TTL_SECONDS)
    return token


async def _get_cached_tenant_token_async(config: FeishuConfig) -> str:
    """获取缓存的 tenant_access_token（带 TTL，异步版本）。"""
    key = config.app_id
    now = time.time()
    cached = _token_cache.get(key)
    if cached and cached[1] > now:
        return cached[0]
    # 未缓存或已过期，异步获取
    token = await _fetch_tenant_access_token_async(config)
    _token_cache[key] = (token, now + _TOKEN_TTL_SECONDS)
    return token


def clear_token_cache() -> None:
    """清除 Token 缓存（测试用）。"""
    _token_cache.clear()


def _parse_feishu_json_code(raw: Any) -> int | None:
    """开放平台 JSON 体中的 ``code`` 字段；无法解析为整数时返回 ``None``。"""
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


async def _async_http_request(
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """异步发送 JSON HTTP 请求并返回解析后的响应体。

    Args:
        method: HTTP 方法（GET / POST）
        url: 请求 URL
        payload: POST 请求体（仅 POST 时使用）
        headers: 请求头

    Returns:
        解析后的 JSON 响应体

    Raises:
        RuntimeError: HTTP 错误、网络错误或 JSON 解析失败
    """
    client = _get_http_client()
    h = {"Content-Type": "application/json; charset=utf-8"}
    if headers:
        h.update(headers)
    try:
        if method.upper() == "POST":
            resp = await client.post(url, json=payload or {}, headers=h)
        else:
            resp = await client.get(url, headers=h)
        resp.raise_for_status()
        body = resp.text
    except httpx.HTTPStatusError as e:
        body = e.response.text
        raise RuntimeError(f"HTTP {e.response.status_code}: {body[:500]}") from e
    except httpx.RequestError as e:
        raise RuntimeError(f"network error: {e}") from e
    try:
        out: dict[str, Any] = json.loads(body)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"invalid JSON from Feishu {method} (len={len(body)}): {body[:300]!r}…"
        ) from e
    return out


async def _async_http_post_json(
    url: str, payload: dict[str, Any], *, headers: dict[str, str] | None = None
) -> dict[str, Any]:
    """异步发送 JSON POST 请求（兼容旧调用，委托给 _async_http_request）。"""
    return await _async_http_request("POST", url, payload=payload, headers=headers)


async def _async_http_get_json(url: str, *, headers: dict[str, str]) -> dict[str, Any]:
    """异步发送 JSON GET 请求（兼容旧调用，委托给 _async_http_request）。"""
    return await _async_http_request("GET", url, headers=headers)


def _http_request(
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """同步发送 JSON HTTP 请求并返回解析后的响应体。

    Args:
        method: HTTP 方法（GET / POST）
        url: 请求 URL
        payload: POST 请求体（仅 POST 时使用）
        headers: 请求头

    Returns:
        解析后的 JSON 响应体

    Raises:
        RuntimeError: HTTP 错误、网络错误或 JSON 解析失败
    """
    import urllib.error
    import urllib.request

    h = {"Content-Type": "application/json; charset=utf-8"}
    if headers:
        h.update(h)

    if method.upper() == "POST":
        data = json.dumps(payload or {}).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST", headers=h)
    else:
        req = urllib.request.Request(url, method="GET", headers=h)

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code}: {body[:500]}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"network error: {e}") from e
    try:
        out: dict[str, Any] = json.loads(body)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"invalid JSON from Feishu {method} (len={len(body)}): {body[:300]!r}…"
        ) from e
    return out


def _http_post_json(
    url: str, payload: dict[str, Any], *, headers: dict[str, str] | None = None
) -> dict[str, Any]:
    """同步发送 JSON POST 请求（兼容旧调用，委托给 _http_request）。"""
    return _http_request("POST", url, payload=payload, headers=headers)


def _http_get_json(url: str, *, headers: dict[str, str]) -> dict[str, Any]:
    """同步发送 JSON GET 请求（兼容旧调用，委托给 _http_request）。"""
    return _http_request("GET", url, headers=headers)


def _fetch_tenant_access_token_sync(config: FeishuConfig) -> str:
    """获取飞书 tenant_access_token（同步版本，用于快速初始化）。"""
    js = _http_post_json(
        _TENANT_TOKEN_URL,
        {"app_id": config.app_id, "app_secret": config.app_secret},
    )
    code = _parse_feishu_json_code(js.get("code"))
    if code is None or code != 0:
        raise RuntimeError(f"tenant_access_token: code={js.get('code')!r} msg={js.get('msg')}")
    tok = str(js.get("tenant_access_token") or "").strip()
    if not tok:
        raise RuntimeError("tenant_access_token: empty tenant_access_token in response")
    return tok


async def _fetch_tenant_access_token_async(config: FeishuConfig) -> str:
    """获取飞书 tenant_access_token（异步版本）。"""
    js = await _async_http_post_json(
        _TENANT_TOKEN_URL,
        {"app_id": config.app_id, "app_secret": config.app_secret},
    )
    code = _parse_feishu_json_code(js.get("code"))
    if code is None or code != 0:
        raise RuntimeError(f"tenant_access_token: code={js.get('code')!r} msg={js.get('msg')}")
    tok = str(js.get("tenant_access_token") or "").strip()
    if not tok:
        raise RuntimeError("tenant_access_token: empty tenant_access_token in response")
    return tok


# 保留旧名称作为同步版本的别名（向后兼容）
_fetch_tenant_access_token = _fetch_tenant_access_token_sync


def get_root_folder_meta(config: FeishuConfig) -> str:
    """调用「获取根文件夹元数据」接口（同步版本）。

    返回根目录 ``folder_token``。
    """
    tenant = _get_cached_tenant_token(config)
    js = _http_get_json(
        _ROOT_FOLDER_META_URL,
        headers={"Authorization": f"Bearer {tenant}"},
    )
    code = _parse_feishu_json_code(js.get("code"))
    if code is None or code != 0:
        raise RuntimeError(
            f"root_folder/meta failed: code={js.get('code')!r} msg={js.get('msg')} "
            f"(若 code=91204 多为无 drive 权限；请为应用开通云盘元数据/读写权限或关闭 FEISHU_DOC_FOLDER_FALLBACK_ROOT_META)"
        )
    data = js.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("root_folder/meta: missing data object")
    token = str(data.get("token") or "").strip()
    if not token:
        raise RuntimeError("root_folder/meta: empty token in response")
    return token


async def get_root_folder_meta_async(config: FeishuConfig) -> str:
    """调用「获取根文件夹元数据」接口（异步版本）。

    返回根目录 ``folder_token``。
    """
    tenant = await _get_cached_tenant_token_async(config)
    js = await _async_http_get_json(
        _ROOT_FOLDER_META_URL,
        headers={"Authorization": f"Bearer {tenant}"},
    )
    code = _parse_feishu_json_code(js.get("code"))
    if code is None or code != 0:
        raise RuntimeError(
            f"root_folder/meta failed: code={js.get('code')!r} msg={js.get('msg')} "
            f"(若 code=91204 多为无 drive 权限；请为应用开通云盘元数据/读写权限或关闭 FEISHU_DOC_FOLDER_FALLBACK_ROOT_META)"
        )
    data = js.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("root_folder/meta: missing data object")
    token = str(data.get("token") or "").strip()
    if not token:
        raise RuntimeError("root_folder/meta: empty token in response")
    return token


def list_folder_files_page(
    config: FeishuConfig,
    *,
    folder_token: str,
    page_token: str | None = None,
    page_size: int = LIST_FILE_PAGE_SIZE,
) -> tuple[list[dict[str, Any]], str | None, bool]:
    """列举某文件夹下一页文件/子文件夹。

    Returns:
        ``(entries, next_page_token, has_more)``；每个 entry 含 ``name``、``token``、``type``（开放平台原始 type 字符串）。
    """
    from lark_oapi.api.drive.v1 import ListFileRequest

    client = build_client(config)
    b = ListFileRequest.builder().folder_token(folder_token).page_size(min(page_size, 200))
    if page_token:
        b = b.page_token(page_token)
    resp = client.drive.v1.file.list(b.build())
    if not resp.success() or not resp.data:
        raise RuntimeError(f"Feishu drive list_file failed: {format_lark_response_error(resp)}")
    files = getattr(resp.data, "files", None) or []
    out: list[dict[str, Any]] = []
    for f in files:
        out.append(
            {
                "name": getattr(f, "name", None) or "",
                "token": getattr(f, "token", None) or "",
                "type": getattr(f, "type", None) or "",
            }
        )
    next_tok = getattr(resp.data, "next_page_token", None)
    has_more = bool(getattr(resp.data, "has_more", False))
    return out, (str(next_tok) if next_tok else None), has_more


__all__ = [
    "LIST_FILE_PAGE_SIZE",
    "get_root_folder_meta",
    "get_root_folder_meta_async",
    "list_folder_files_page",
    "clear_client_cache",
    "clear_token_cache",
    "reset_http_client",
]
