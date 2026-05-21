"""Benchmarking utility: local serialization sample for L1 performance scenario S7.

Not part of the runtime agent pipeline. Only used by performance tests.
"""

from __future__ import annotations

import json
from typing import Any

from miniagent.memory.context import DefaultContextManager


def serialize_exec_payload_sample(
    tools: list[dict[str, Any]],
    *,
    user_turn_pairs: int = 6,
) -> tuple[int, int]:
    """构建一轮执行风格的消息列表并对 messages/tools 做 ``json.dumps``。

    与 ``execute_plan`` 内发往 ``chat.completions.create`` 的载荷形状对齐（不含流式参数），
    仅用于本地 CPU/分配冒烟。

    Returns:
        ``(len(messages_json), len(tools_json))`` 字节长度（UTF-8 近似由调用方处理）。
    """
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
