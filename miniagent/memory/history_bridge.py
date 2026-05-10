"""会话历史 ↔ Chat Completions：磁盘可存扩展 ``role``（如 ``thinking``），调用 API 前做清洗。

``conversation_history_for_llm`` 将内部格式映射为 OpenAI 兼容的 ``user``/``assistant``/
``system``/``tool``；含归档标记的 system 消息会保留为简短衔接说明。
"""

from __future__ import annotations

import copy
from typing import Any


def conversation_history_for_llm(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """将持久化历史转为 Chat Completions 可接受的消息列表。

    - ``role=thinking`` → 合并为 assistant 文本块（标注为思考记录）
    - 去掉以下划线开头的元数据键
    """
    out: list[dict[str, Any]] = []
    for m in history:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role == "thinking":
            c = (m.get("content") or "").strip()
            if c:
                out.append(
                    {"role": "assistant", "content": f"（思考过程）\n{c}"}
                )
            continue
        if role == "system" and m.get("_history_archive_marker"):
            brief = (m.get("content") or "").strip()
            if brief:
                out.append({"role": "system", "content": brief})
            continue
        clean = {
            k: copy.deepcopy(v)
            for k, v in m.items()
            if isinstance(k, str) and not k.startswith("_")
        }
        if clean.get("role") not in ("user", "assistant", "system", "tool"):
            continue
        out.append(clean)
    return out


def estimate_history_messages_tokens(history: list[dict[str, Any]]) -> int:
    """粗略 token 估算（用于归档触发）。"""
    from miniagent.memory.context import estimate_tokens

    n = 0
    for m in history:
        if not isinstance(m, dict):
            continue
        c = m.get("content")
        if isinstance(c, str):
            n += estimate_tokens(c)
        if m.get("role") == "assistant" and m.get("tool_calls"):
            n += estimate_tokens(str(m.get("tool_calls")))
        n += 5
    return n
