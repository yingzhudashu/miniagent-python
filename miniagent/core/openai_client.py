"""共享 AsyncOpenAI 客户端 — 进程内惰性单例。

供 ``generate_plan`` / ``execute_plan`` / ``run_agent`` 在调用方未传入 ``client=`` 时回落使用；
``RuntimeContext.openai_client`` 在 ``engine.main.unified_main`` 中通常设为同一实例，保证全链路一致。

**测试**：调用 ``reset_shared_async_openai_for_tests()`` 可清空缓存，便于注入 stub 或避免用例间泄漏。
"""

from __future__ import annotations

import os

from openai import AsyncOpenAI

from miniagent.infrastructure.debug_ndjson import safe_agent_debug_log
from miniagent.infrastructure.json_config import get_config

_shared: AsyncOpenAI | None = None


def get_shared_async_openai() -> AsyncOpenAI:
    """获取进程内共享的 AsyncOpenAI 客户端（惰性单例）。

    首次调用时从环境变量读取 API 密钥并初始化客户端，后续调用直接返回缓存实例。
    测试场景可使用 ``execute_plan(..., client=...)`` 注入自定义客户端，或调用
    ``reset_shared_async_openai_for_tests()`` 清空缓存。

    Returns:
        AsyncOpenAI: 共享的异步 OpenAI 客户端实例

    Raises:
        RuntimeError: 未配置 OPENAI_API_KEY 时抛出，提示用户设置凭据

    Note:
        - API 密钥优先从 ``OPENAI_API_KEY`` 环境变量读取
        - base_url 从 ``config.user.json`` 或 ``MINIAGENT_MODEL_BASE_URL`` 读取
        - 支持兼容 OpenAI API 的第三方服务（如 Azure、本地模型）
        - 网络可靠性：添加超时配置和重试机制
    """
    global _shared
    if _shared is None:
        # API密钥必须从环境变量读取（敏感凭据）
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
                "请在 config.user.json 的 secrets 部分或环境中设置 OPENAI_API_KEY；使用国内/自建兼容端点时请同时设置 MINIAGENT_MODEL_BASE_URL。"
            ) from None
        # base_url从JSON配置读取（支持环境变量覆盖）
        base_url = get_config("model.base_url", None)

        # 网络可靠性：从配置读取超时和重试参数
        http_timeout = float(get_config("agent.http_timeout", 120.0))
        retry_count = int(get_config("model.retry_count", 2))

        # OpenAI SDK 原生支持 timeout 和 max_retries
        import httpx
        _shared = AsyncOpenAI(
            api_key=key,
            base_url=base_url,
            timeout=httpx.Timeout(http_timeout, connect=30.0),
            max_retries=retry_count,
        )
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


def reset_shared_async_openai_for_tests() -> None:
    """清空缓存，仅供测试。"""
    global _shared
    _shared = None


__all__ = ["get_shared_async_openai", "reset_shared_async_openai_for_tests"]
