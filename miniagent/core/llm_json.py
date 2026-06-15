"""Shared LLM JSON helper — 供 control 层模块复用的 JSON 解析工具。

本模块提供以下函数：

1. ``llm_json()`` — 调用 LLM 并解析 JSON 回复（需网络请求）
2. ``parse_llm_json_response()`` — 解析 LLM 返回的 JSON 字本，处理 markdown 围栏、截取大括号

使用场景：
- ``problem_solver.py`` 的 _analyze_problem / _reflect
- ``requirement_clarifier.py`` 的 clarify
- ``planner.py`` 的规划输出解析
- ``task_classifier.py`` 的难度分类解析

**注意**：``llm_json()`` 需要网络请求，单元测试中应通过 patch 或注入 Mock client 避免真实调用。
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from miniagent.core._openai_compat import ensure_json_object_user_message
from miniagent.infrastructure.json_config import get_config

_logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from openai import AsyncOpenAI


def parse_llm_json_response(content: str, *, strip_fence: bool = True) -> dict[str, Any]:
    """解析 LLM 返回的 JSON 文本，处理常见格式问题。

    处理策略：
    1. 去除 markdown 围栏（```json / ```）
    2. 尝试直接解析
    3. 失败时截取首尾大括号内容再次解析

    Args:
        content: LLM 返回的文本内容
        strip_fence: 是否去除 markdown 围栏（默认 True）

    Returns:
        解析后的 JSON 字典

    Raises:
        json.JSONDecodeError: 解析失败时抛出
    """
    text = content.strip()

    # 去除 markdown 围栏
    if strip_fence and text.startswith("```"):
        # 常见围栏格式：```json\n{...}\n``` 或 ```\n{...}\n```
        lines = text.split("\n")
        # 移除首行的 ```json 或 ```
        if lines[0].startswith("```"):
            lines = lines[1:]
        # 移除末行的 ```
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    # 尝试直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 失败时截取首尾大括号
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError as e:
                _logger.debug("JSON修复失败: %s", e)
        # 无法修复，重新抛出原始异常
        raise


async def llm_json(
    prompt: str,
    system: str,
    client: AsyncOpenAI | None = None,
    model: str | None = None,
    raise_on_error: bool = False,
    *,
    max_tokens: int | None = None,
    thinking_level: str | None = None,
    thinking_budget: int | None = None,
    trace_phase: str | None = None,
    trace_session_key: str | None = None,
) -> dict[str, Any]:
    """调用 LLM 并解析 JSON 回复。

    Args:
        prompt: 用户提示
        system: 系统提示
        client: LLM 客户端（None 时回落到共享工厂）
        model: 模型名（None 时读取 ``MINIAGENT_MODEL_MODEL`` 环境变量，回落到 ``gpt-4o-mini``）
        raise_on_error: 解析失败时是否抛出异常（默认 False，返回空字典）
        max_tokens: 可选输出 token 上限。结构化 JSON 评估/分类类调用通常不需要大输出，
            限制后可显著降低延迟（实测 reflect -34%）。None 时不传，沿用服务端默认。
        thinking_level: 可选思考档位（如 ``"low"`` / ``"disabled"``）。JSON 评分类任务无需深度思考；
            提供时经 ``build_thinking_extra_body`` 注入 ``extra_body``，对不支持的端点自动无副作用。
        thinking_budget: 与 ``thinking_level`` 配套的思考预算（token）。
        trace_phase: 可选 trace 阶段标签（如 ``"reflect"`` / ``"clarify"``）；提供时发出
            ``llm.request`` / ``llm.response`` 事件，消除该阶段的 trace 盲区。
        trace_session_key: trace 事件归属的会话标识。

    Returns:
        解析后的 JSON 字典；解析失败时：
        - raise_on_error=False：返回空字典 {}
        - raise_on_error=True：抛出 json.JSONDecodeError

    Raises:
        json.JSONDecodeError: 当 raise_on_error=True 且解析失败时抛出

    Note:
        OpenAI API 要求：使用 response_format=json_object 时，
        消息中必须包含 "json" 这个词（不区分大小写）。
        本函数会自动检查并添加必要的提示。
    """
    from miniagent.core.openai_client import get_shared_async_openai

    llm = client or get_shared_async_openai()
    if model is None:
        model = get_config("model.model", "gpt-4o-mini")

    # Some compatible endpoints require a user/input message to mention "json".
    messages = ensure_json_object_user_message(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]
    )

    create_kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "response_format": {"type": "json_object"},
    }
    if max_tokens is not None:
        create_kwargs["max_tokens"] = int(max_tokens)
    if thinking_level is not None:
        # 经 vendor 助手按端点能力注入；不支持的端点返回空 dict，无副作用。
        from miniagent.core.vendor.qwen_extra import build_thinking_extra_body

        base_url = get_config("model.base_url", None)
        extra = build_thinking_extra_body(
            base_url,
            thinking_level,
            int(thinking_budget or 0),
            model_overrides_extra={},
        )
        if extra:
            create_kwargs["extra_body"] = extra

    if trace_phase:
        from miniagent.infrastructure.tracing import emit_trace

        emit_trace(
            {
                "type": "llm.request",
                "phase": trace_phase,
                "session_key": trace_session_key or "default",
                "model": model,
                "json_object": True,
            }
        )

    resp = await llm.chat.completions.create(**create_kwargs)

    if trace_phase:
        from miniagent.infrastructure.tracing import emit_trace

        _usage = getattr(resp, "usage", None)
        emit_trace(
            {
                "type": "llm.response",
                "phase": trace_phase,
                "session_key": trace_session_key or "default",
                "model": model,
                "usage": _usage.model_dump()
                if _usage is not None and hasattr(_usage, "model_dump")
                else None,
            }
        )

    text = resp.choices[0].message.content or "{}"
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logging.getLogger(__name__).warning("LLM 返回的 JSON 解析失败: %s", text[:200])
        if raise_on_error:
            raise
        return {}


__all__ = ["llm_json", "parse_llm_json_response"]
