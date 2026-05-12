"""共享 AsyncOpenAI 客户端 — 进程内惰性单例。

供 ``generate_plan`` / ``execute_plan`` / ``run_agent`` 在调用方未传入 ``client=`` 时回落使用；
``RuntimeContext.openai_client`` 在 ``compat.unified_entry`` 中通常设为同一实例，保证全链路一致。

**测试**：调用 ``reset_shared_async_openai_for_tests()`` 可清空缓存，便于注入 stub 或避免用例间泄漏。
"""

from __future__ import annotations

import os

from openai import AsyncOpenAI

_shared: AsyncOpenAI | None = None


def get_shared_async_openai() -> AsyncOpenAI:
    """进程内惰性单例；测试可改为注入 ``execute_plan(..., client=...)`` / ``generate_plan(..., client=...)``。"""
    global _shared
    if _shared is None:
        key = (os.environ.get("OPENAI_API_KEY") or "").strip()
        if not key:
            # #region agent log
            try:
                from miniagent.infrastructure.debug_ndjson import agent_debug_log

                agent_debug_log(
                    hypothesis_id="C",
                    location="openai_client.py:get_shared_async_openai",
                    message="missing_openai_api_key_abort",
                    data={
                        "miniagent_config_set": bool(
                            (os.environ.get("MINIAGENT_CONFIG") or "").strip()
                        ),
                    },
                )
            except Exception:
                pass
            # #endregion
            raise RuntimeError(
                "未配置 OPENAI_API_KEY，无法调用 LLM（任务分类、规划、对话均依赖）。"
                "请在 .env 或环境中设置 OPENAI_API_KEY；若使用 MINIAGENT_CONFIG，请在 JSON 的 providers 中配置 "
                "apiKey（加载后会写入 OPENAI_API_KEY）。使用国内/自建兼容端点时请同时设置 OPENAI_BASE_URL。"
            ) from None
        _shared = AsyncOpenAI(
            api_key=key,
            base_url=os.environ.get("OPENAI_BASE_URL") or None,
        )
        # #region agent log
        try:
            from miniagent.infrastructure.debug_ndjson import agent_debug_log

            bu = (os.environ.get("OPENAI_BASE_URL") or "").strip()
            host = ""
            if bu and "//" in bu:
                host = bu.split("//", 1)[1].split("/", 1)[0][:120]
            elif bu:
                host = bu[:120]
            agent_debug_log(
                hypothesis_id="A",
                location="openai_client.py:get_shared_async_openai",
                message="shared_async_openai_created",
                data={
                    "base_url_nonempty": bool(bu),
                    "base_url_host_snip": host,
                    "api_key_len": len((os.environ.get("OPENAI_API_KEY") or "").strip()),
                },
            )
        except Exception:
            pass
        # #endregion
    return _shared


def reset_shared_async_openai_for_tests() -> None:
    """清空缓存，仅供测试。"""
    global _shared
    _shared = None


__all__ = ["get_shared_async_openai", "reset_shared_async_openai_for_tests"]
