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
import httpx
from typing import Any


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
    last_error: str = ""

    for attempt in range(max_retries):
        try:
            # 构建请求参数
            request_args: dict[str, Any] = {}
            if headers:
                request_args["headers"] = headers
            if timeout is not None:
                request_args["timeout"] = timeout

            # 发送请求
            if method.upper() == "POST":
                request_args["json"] = payload or {}
                resp = await client.post(url, **request_args)
            elif method.upper() == "GET":
                resp = await client.get(url, **request_args)
            else:
                resp = await client.request(method, url, **request_args)

            # 检查HTTP状态
            resp.raise_for_status()
            return resp

        except httpx.HTTPStatusError as e:
            # 4xx错误：客户端错误，不重试
            if e.response.status_code < 500:
                raise RuntimeError(
                    f"HTTP {e.response.status_code} 错误: {e.response.text[:500]}"
                ) from e

            # 5xx错误：服务器错误，重试
            last_error = f"HTTP {e.response.status_code}: {e.response.text[:200]}"
            if attempt < max_retries - 1:
                delay = backoff_factor * (2 ** attempt)
                await asyncio.sleep(delay)
                continue
            raise RuntimeError(
                f"HTTP请求失败（重试{max_retries}次后）: {last_error}"
            ) from e

        except httpx.TimeoutException as e:
            # 超时错误，重试
            last_error = f"请求超时: {e}"
            if attempt < max_retries - 1:
                delay = backoff_factor * (2 ** attempt)
                await asyncio.sleep(delay)
                continue
            raise RuntimeError(
                f"HTTP请求超时（重试{max_retries}次后）: {last_error}"
            ) from e

        except httpx.RequestError as e:
            # 网络错误（连接失败、DNS错误等），重试
            last_error = f"网络错误: {e}"
            if attempt < max_retries - 1:
                delay = backoff_factor * (2 ** attempt)
                await asyncio.sleep(delay)
                continue
            raise RuntimeError(
                f"网络请求失败（重试{max_retries}次后）: {last_error}"
            ) from e

    # 不应该到达这里
    raise RuntimeError(f"HTTP请求失败: {last_error}")


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