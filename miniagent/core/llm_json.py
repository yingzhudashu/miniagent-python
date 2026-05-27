"""Shared LLM JSON helper — 供 control 层模块复用的 JSON 解析工具。

本模块提供 ``llm_json()`` 函数，封装：
1. 调用 LLM（支持注入 client 或回落到共享工厂）
2. 设置 ``response_format={"type": "json_object"}``
3. 解析返回文本为 JSON 字典（失败时返回空字典）

使用场景：
- ``problem_solver.py`` 的 _analyze_problem / _reflect
- ``requirement_clarifier.py`` 的 clarify

**注意**：此函数需要 LLM 调用能力，单元测试中应通过 patch 或注入 Mock client 避免真实网络请求。
"""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openai import AsyncOpenAI


async def llm_json(
    prompt: str,
    system: str,
    client: AsyncOpenAI | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """调用 LLM 并解析 JSON 回复。

    Args:
        prompt: 用户提示
        system: 系统提示
        client: LLM 客户端（None 时回落到共享工厂）
        model: 模型名（None 时读取 ``OPENAI_MODEL`` 环境变量，回落到 ``gpt-4o-mini``）

    Returns:
        解析后的 JSON 字典；解析失败返回空字典。
    """
    from miniagent.core.openai_client import get_shared_async_openai

    llm = client or get_shared_async_openai()
    if model is None:
        model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    resp = await llm.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
    )
    text = resp.choices[0].message.content or "{}"
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logging.getLogger(__name__).warning("LLM 返回的 JSON 解析失败: %s", text[:200])
        return {}


__all__ = ["llm_json"]
