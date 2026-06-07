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
) -> dict[str, Any]:
    """调用 LLM 并解析 JSON 回复。

    Args:
        prompt: 用户提示
        system: 系统提示
        client: LLM 客户端（None 时回落到共享工厂）
        model: 模型名（None 时读取 ``MINIAGENT_MODEL_MODEL`` 环境变量，回落到 ``gpt-4o-mini``）

    Returns:
        解析后的 JSON 字典；解析失败返回空字典。

    Note:
        OpenAI API 要求：使用 response_format=json_object 时，
        消息中必须包含 "json" 这个词（不区分大小写）。
        本函数会自动检查并添加必要的提示。
    """
    from miniagent.core.openai_client import get_shared_async_openai

    llm = client or get_shared_async_openai()
    if model is None:
        model = get_config("model.model", "gpt-4o-mini")

    # OpenAI API 要求：使用 json_object 模式时，消息中必须包含 "json" 这个词
    # 检查 system + prompt 中是否有 "json"（不区分大小写）
    combined = (system + prompt).lower()
    use_json_object = "json" in combined

    # 如果没有 "json" 这个词，在 system 提示中添加 JSON 输出要求
    actual_system = system
    if not use_json_object:
        actual_system = system + "\n\n请以 JSON 格式返回结果。"
        use_json_object = True  # 现在消息中包含了 "json"

    resp = await llm.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": actual_system},
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


__all__ = ["llm_json", "parse_llm_json_response"]
