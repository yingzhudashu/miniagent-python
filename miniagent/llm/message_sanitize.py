"""Chat Completions 请求体清洗 — 剥离 ``_*`` 自定义键

内部管线可能在 ``messages`` 中附带以下划线开头的元数据（例如 ``_tool_calls_tokens``、
``_injected``）；OpenAI 兼容 API 会拒绝未知顶层字段。本模块在每次
``chat.completions.create`` 前浅拷贝并剔除这些键，同时清理 ``tool_calls[]`` 内嵌 dict
的同名字段。

与 ``memory.history_bridge.conversation_history_for_llm`` 分工：后者在加载持久化历史时做
角色映射与业务裁剪；本模块在 executor 每轮 LLM 请求前再次清洗，覆盖运行时新写入的内部键。

若新增消息字段，要么使用 API 认可的名字，要么保留 ``_`` 前缀并依赖此处剥离。
"""

from __future__ import annotations

from typing import Any


def strip_leading_underscore_keys_from_messages(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """浅拷贝每条消息，移除顶层及 ``tool_calls[]`` 项内的 ``_*`` 键。

    处理规则：
    - 仅剥离键名为 ``str`` 且以 ``_`` 开头的字段
    - 清洗深度为两层：消息顶层 + ``tool_calls[]`` 中的 dict 项
    - 更深层嵌套（如 ``function``、``content`` 数组元素）不做递归清洗
    - 非 ``dict`` 的消息项会被跳过，不进入返回列表

    Args:
        messages: 待发送给 Chat Completions API 的消息列表

    Returns:
        新消息列表；每条消息为浅拷贝，不修改 ``messages`` 或其中的原始 dict

    Note:
        - 嵌套对象（``content``、``function.arguments`` 等）与输出共享引用
        - ``tool_calls`` 中非 dict 项原样保留

    Example:
        >>> strip_leading_underscore_keys_from_messages(
        ...     [{"role": "user", "content": "hi", "_internal": 1}]
        ... )
        [{'role': 'user', 'content': 'hi'}]
    """
    out: list[dict[str, Any]] = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        d: dict[str, Any] = {
            k: v for k, v in m.items() if isinstance(k, str) and not k.startswith("_")
        }
        tc = d.get("tool_calls")
        if isinstance(tc, list):
            cleaned: list[Any] = []
            for item in tc:
                if isinstance(item, dict):
                    cleaned.append(
                        {
                            kk: vv
                            for kk, vv in item.items()
                            if isinstance(kk, str) and not kk.startswith("_")
                        }
                    )
                else:
                    cleaned.append(item)
            d["tool_calls"] = cleaned
        out.append(d)
    return out


__all__ = ["strip_leading_underscore_keys_from_messages"]
