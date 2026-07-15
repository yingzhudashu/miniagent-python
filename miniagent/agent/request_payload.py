"""Benchmarking utility: local serialization sample for L1 performance scenario S7.

Not part of the runtime agent pipeline. Only used by performance tests.

See also:
    - ``docs/PERFORMANCE.md`` §6（S7 索引）
    - ``tests/test_perf_synthetic.py::test_s7_exec_payload_json_serialize_median_under_cap``
"""

from __future__ import annotations

import json
from typing import Any

from miniagent.agent.context import DefaultContextManager


def serialize_exec_payload_sample(
    tools: list[dict[str, Any]],
    *,
    user_turn_pairs: int = 6,
) -> tuple[int, int]:
    """构建执行风格 messages 并对 messages/tools 做 ``json.dumps``（S7 perf 样本）。

    与 ``execute_plan`` 交给统一 LLM transport 的 ``messages`` / ``tools``
    **传参形状**对齐：messages 来自 ``DefaultContextManager``，tools 单独序列化。
    不含流式参数、model kwargs 或网络 I/O。

    Args:
        tools: OpenAI 风格 tool schema 列表（与 executor 传入 LLM 的结构相同）。
        user_turn_pairs: 模拟的 assistant/user 往返轮数（默认 6）；``0`` 时仅保留
            ``init`` 产生的 system + user 两条消息。

    Returns:
        ``(messages_json_len, tools_json_len)``：``json.dumps`` 结果字符串的**字符数**
        （``len(str)``）。样本内容为 ASCII，字符数与 UTF-8 字节数一致；若将来改用
        非 BMP 字符，二者可能不同。

    Raises:
        ValueError: ``user_turn_pairs`` 为负数。

    Note:
        这是简化的 perf 冒烟样本，**未**模拟以下内容：

        - ``role=tool`` 工具结果回注
        - 带 ``tool_calls`` 的 assistant 消息
        - ``build_current_turn_user_context`` 中的记忆/知识库/时区等动态 user 内容
        - 对话历史恢复与 thinking → assistant 映射

        ``compress_threshold=0.99`` 与较大 ``context_window`` 用于避免压缩干扰序列化耗时。

    Example:
        >>> tools = [{"type": "function", "function": {"name": "t", "parameters": {}}}]
        >>> ml, tl = serialize_exec_payload_sample(tools, user_turn_pairs=2)
        >>> ml > 0 and tl > 0
        True
    """
    if user_turn_pairs < 0:
        raise ValueError(f"user_turn_pairs must be >= 0, got {user_turn_pairs}")

    cm = DefaultContextManager(
        context_window=128_000,
        compress_threshold=0.99,
        tools=tools,  # type: ignore[arg-type]
        overflow_strategy="summarize",
    )
    cm.init("system " * 400, "user " * 400)
    for _ in range(user_turn_pairs):
        cm.append({"role": "assistant", "content": "a" * 400})
        cm.append({"role": "user", "content": "b" * 200})
    msgs = cm.get_messages()
    mj = json.dumps(msgs, ensure_ascii=False)
    tj = json.dumps(tools, ensure_ascii=False)
    return len(mj), len(tj)


__all__ = ["serialize_exec_payload_sample"]
