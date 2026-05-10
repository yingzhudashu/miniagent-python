"""共享 AsyncOpenAI 客户端 — 供规划器与执行器在「未注入 client」时回落使用。"""

from __future__ import annotations

import os

from openai import AsyncOpenAI

_shared: AsyncOpenAI | None = None


def get_shared_async_openai() -> AsyncOpenAI:
    """进程内惰性单例；测试可改为注入 ``execute_plan(..., client=...)`` / ``generate_plan(..., client=...)``。"""
    global _shared
    if _shared is None:
        _shared = AsyncOpenAI(
            api_key=os.environ.get("OPENAI_API_KEY", ""),
            base_url=os.environ.get("OPENAI_BASE_URL") or None,
        )
    return _shared


def reset_shared_async_openai_for_tests() -> None:
    """清空缓存，仅供测试。"""
    global _shared
    _shared = None


__all__ = ["get_shared_async_openai", "reset_shared_async_openai_for_tests"]
