"""共享 AsyncOpenAI 客户端 — 进程内惰性单例。

供 ``generate_plan`` / ``execute_plan`` / ``UnifiedEngine`` 等在调用方未传入 ``client=`` 时回落使用；
``RuntimeContext.openai_client`` 在 ``compat.unified_entry`` 中通常设为同一实例，保证全链路一致。

配置热更新：``reload_runtime_config()`` 会调用 :func:`sync_runtime_context_openai_client` 重建客户端并同步
已登记的 ``RuntimeContext.openai_client``。

**测试**：调用 ``reset_shared_async_openai_for_tests()`` 可清空缓存，便于注入 stub 或避免用例间泄漏。
"""

from __future__ import annotations

import asyncio
import os

import httpx
from openai import AsyncOpenAI

from miniagent.infrastructure.debug_ndjson import safe_agent_debug_log
from miniagent.infrastructure.json_config import get_config

_shared: AsyncOpenAI | None = None

_CONNECT_TIMEOUT_SEC = 30.0


def _read_http_timeout() -> float:
    raw = get_config("agent.http_timeout", 120.0)
    try:
        timeout = float(raw)
    except (TypeError, ValueError):
        raise RuntimeError(
            f"agent.http_timeout 配置无效（{raw!r}），需要数字（秒）。"
        ) from None
    if timeout <= 0:
        raise RuntimeError("agent.http_timeout 必须大于 0。")
    return timeout


def _read_retry_count() -> int:
    raw = get_config("model.retry_count", 2)
    try:
        count = int(raw)
    except (TypeError, ValueError):
        raise RuntimeError(
            f"model.retry_count 配置无效（{raw!r}），需要整数。"
        ) from None
    if count < 0:
        raise RuntimeError("model.retry_count 不能为负数。")
    return count


def _schedule_client_close(client: AsyncOpenAI) -> None:
    """尽力关闭旧客户端底层 HTTP 连接（无运行中事件循环时依赖 GC）。"""
    close = getattr(client, "close", None)
    if not callable(close):
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if loop.is_running():
        loop.create_task(close())


def _build_async_openai(api_key: str) -> AsyncOpenAI:
    base_url = get_config("model.base_url", None)
    http_timeout = _read_http_timeout()
    retry_count = _read_retry_count()
    return AsyncOpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=httpx.Timeout(http_timeout, connect=_CONNECT_TIMEOUT_SEC),
        max_retries=retry_count,
    )


def get_shared_async_openai() -> AsyncOpenAI:
    """获取进程内共享的 AsyncOpenAI 客户端（惰性单例）。

    首次调用时从环境变量读取 API 密钥并初始化客户端，后续调用直接返回缓存实例。
    测试场景可使用 ``execute_plan(..., client=...)`` 注入自定义客户端，或调用
    ``reset_shared_async_openai_for_tests()`` 清空缓存。

    Returns:
        AsyncOpenAI: 共享的异步 OpenAI 客户端实例

    Raises:
        RuntimeError: 未配置 ``OPENAI_API_KEY``，或 ``agent.http_timeout`` /
            ``model.retry_count`` 配置无效时抛出

    Note:
        - API 密钥**仅**从 ``OPENAI_API_KEY`` 环境变量读取；入口须先调用
          ``load_secrets_from_project_root()`` 将 ``config.user.json`` 的
          ``secrets.openai_api_key`` 桥接到该变量
        - ``model.base_url`` 从 JSON 配置读取（``config.user.json`` 覆盖
          ``config.defaults.json``；不支持 ``MINIAGENT_*`` 环境变量覆盖）
        - 支持兼容 OpenAI API 的第三方服务（如 Azure、本地模型）
        - 超时与重试：``agent.http_timeout``、``model.retry_count``
    """
    global _shared
    if _shared is None:
        key = (os.environ.get("OPENAI_API_KEY") or "").strip()
        if not key:
            safe_agent_debug_log(
                hypothesis_id="C",
                location="openai_client.py:get_shared_async_openai",
                message="missing_openai_api_key_abort",
                data={},
            )
            raise RuntimeError(
                "未配置 OPENAI_API_KEY，无法调用 LLM（任务分类、规划、对话均依赖）。"
                "请在 config.user.json 的 secrets.openai_api_key 填写 API 密钥；"
                "使用国内/自建兼容端点时请同时设置 model.base_url。"
            ) from None

        _shared = _build_async_openai(key)
        base_url = get_config("model.base_url", None)
        bu = (base_url or "").strip()
        host = ""
        if bu and "//" in bu:
            host = bu.split("//", 1)[1].split("/", 1)[0][:120]
        elif bu:
            host = bu[:120]
        safe_agent_debug_log(
            hypothesis_id="A",
            location="openai_client.py:get_shared_async_openai",
            message="shared_async_openai_created",
            data={
                "base_url_nonempty": bool(bu),
                "base_url_host_snip": host,
                "api_key_len": len(key),
            },
        )
    return _shared


def invalidate_shared_async_openai() -> None:
    """丢弃已缓存的 AsyncOpenAI 客户端，下次调用时按当前环境变量与配置重建。

    会尽力在事件循环中 ``close()`` 旧客户端以释放连接。不会自动更新
    ``RuntimeContext.openai_client``；配置热更新请使用
    :func:`sync_runtime_context_openai_client` 或 ``reload_runtime_config()``。
    """
    global _shared
    if _shared is not None:
        _schedule_client_close(_shared)
    _shared = None


def sync_runtime_context_openai_client() -> None:
    """清空客户端缓存并按当前配置重建，同步刷新已登记的 ``RuntimeContext``。"""
    invalidate_shared_async_openai()
    from miniagent.runtime.context import get_runtime_context

    ctx = get_runtime_context()
    if ctx is None:
        return
    try:
        ctx.openai_client = get_shared_async_openai()
    except RuntimeError:
        ctx.openai_client = None


def reset_shared_async_openai_for_tests() -> None:
    """清空缓存（与 :func:`invalidate_shared_async_openai` 相同），供测试 teardown 使用。"""
    invalidate_shared_async_openai()


__all__ = [
    "get_shared_async_openai",
    "invalidate_shared_async_openai",
    "sync_runtime_context_openai_client",
    "reset_shared_async_openai_for_tests",
]
