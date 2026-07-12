"""HTTP请求重试工具 - 指数退避策略。

提供统一的HTTP重试机制，用于：
- embedding_search.py: 嵌入API调用
- clawhub_client.py: ClawHub API调用
- 其他需要可靠HTTP连接的场景

重试策略：
- 5xx错误：重试（服务器错误）
- 4xx错误：不重试（客户端错误）
- 网络错误：重试（连接失败、超时）

默认参数：
- max_retries: 3次
- backoff_factor: 1.0（指数退避基数）
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx


async def _send_http_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None,
    headers: dict[str, str] | None,
    timeout: float | None,
) -> httpx.Response:
    """执行单次 HTTP 请求，不包含重试或状态码策略。"""
    request_args: dict[str, Any] = {}
    if headers:
        request_args["headers"] = headers
    if timeout is not None:
        request_args["timeout"] = timeout
    normalized_method = method.upper()
    if normalized_method == "POST":
        return await client.post(url, json=payload or {}, **request_args)
    if normalized_method == "GET":
        return await client.get(url, **request_args)
    return await client.request(method, url, **request_args)


async def _retry_or_raise(
    *,
    error: httpx.RequestError,
    message: str,
    failure_prefix: str,
    attempt: int,
    max_retries: int,
    backoff_factor: float,
) -> None:
    """未到重试上限时退避，否则保留原异常链抛出稳定用户错误。"""
    if attempt < max_retries - 1:
        await asyncio.sleep(backoff_factor * (2**attempt))
        return
    raise RuntimeError(f"{failure_prefix}（重试{max_retries}次后）: {message}") from error


async def async_http_request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    max_retries: int = 3,
    backoff_factor: float = 1.0,
    timeout: float | None = None,
) -> httpx.Response:
    """带重试的异步HTTP请求。

    Args:
        client: httpx.AsyncClient 实例（连接池复用）
        method: HTTP方法（GET/POST）
        url: 请求URL
        payload: POST请求体
        headers: 请求头
        max_retries: 最大重试次数（默认3）
        backoff_factor: 退避因子（默认1.0，指数增长）
        timeout: 单次请求超时（可选）

    Returns:
        httpx.Response 对象

    Raises:
        RuntimeError: 最终失败时的错误信息

    Example:
        client = await _get_http_client()
        resp = await async_http_request_with_retry(
            client, "POST", url,
            payload={"model": "gpt-4", "input": "text"},
            headers={"Authorization": "Bearer key"},
        )
        data = resp.json()
    """
    for attempt in range(max_retries):
        try:
            response = await _send_http_request(
                client,
                method,
                url,
                payload=payload,
                headers=headers,
                timeout=timeout,
            )
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as error:
            if error.response.status_code < 500:
                raise RuntimeError(
                    f"HTTP {error.response.status_code} 错误: {error.response.text[:500]}"
                ) from error
            await _retry_or_raise(
                error=error,
                message=f"HTTP {error.response.status_code}: {error.response.text[:200]}",
                failure_prefix="HTTP请求失败",
                attempt=attempt,
                max_retries=max_retries,
                backoff_factor=backoff_factor,
            )
        except httpx.TimeoutException as error:
            await _retry_or_raise(
                error=error,
                message=f"请求超时: {error}",
                failure_prefix="HTTP请求超时",
                attempt=attempt,
                max_retries=max_retries,
                backoff_factor=backoff_factor,
            )
        except httpx.RequestError as error:
            await _retry_or_raise(
                error=error,
                message=f"网络错误: {error}",
                failure_prefix="网络请求失败",
                attempt=attempt,
                max_retries=max_retries,
                backoff_factor=backoff_factor,
            )
    raise RuntimeError("HTTP请求失败: 未执行任何请求")


async def async_http_get_json_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    max_retries: int = 3,
    backoff_factor: float = 1.0,
) -> dict[str, Any]:
    """带重试的GET请求，返回JSON。

    Args:
        client: httpx.AsyncClient 实例
        url: 请求URL
        headers: 请求头
        max_retries: 最大重试次数
        backoff_factor: 退避因子

    Returns:
        解析后的JSON字典
    """
    resp = await async_http_request_with_retry(
        client, "GET", url,
        headers=headers,
        max_retries=max_retries,
        backoff_factor=backoff_factor,
    )
    return resp.json()


async def async_http_post_json_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    max_retries: int = 3,
    backoff_factor: float = 1.0,
) -> dict[str, Any]:
    """带重试的POST请求，返回JSON。

    Args:
        client: httpx.AsyncClient 实例
        url: 请求URL
        payload: POST请求体
        headers: 请求头
        max_retries: 最大重试次数
        backoff_factor: 退避因子

    Returns:
        解析后的JSON字典
    """
    resp = await async_http_request_with_retry(
        client, "POST", url,
        payload=payload,
        headers=headers,
        max_retries=max_retries,
        backoff_factor=backoff_factor,
    )
    return resp.json()


__all__ = [
    "async_http_request_with_retry",
    "async_http_get_json_with_retry",
    "async_http_post_json_with_retry",
]
