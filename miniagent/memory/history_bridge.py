"""会话历史 ↔ Chat Completions：磁盘可存扩展 ``role``（如 ``thinking``），调用 API 前做清洗。

``conversation_history_for_llm`` 将内部格式映射为 OpenAI 兼容的 ``user``/``assistant``/
``system``/``tool``；含归档标记的 system 消息会保留为简短衔接说明。
"""

from __future__ import annotations

import copy
import os
from typing import Any


def _thinking_for_llm_max_chars() -> int:
    """注入 API 前允许保留的思考文本最大字符数（``MINI_AGENT_THINKING_FOR_LLM_MAX_CHARS``）。"""
    raw = os.environ.get("MINI_AGENT_THINKING_FOR_LLM_MAX_CHARS", "").strip()
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    return 10_000


def _truncate_thinking_for_llm(content: str, max_chars: int) -> str:
    """截断过长思考正文并附加提示，避免占满上下文窗口。"""
    if max_chars <= 0 or len(content) <= max_chars:
        return content
    tail = content[:max_chars].rstrip()
    note = "\n\n…（思考记录已截断供上下文窗口使用；完整内容见会话 history.json）"
    return tail + note


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
                capped = _truncate_thinking_for_llm(c, _thinking_for_llm_max_chars())
                out.append(
                    {"role": "assistant", "content": f"（思考过程）\n{capped}"}
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
    """粗略 token 估算（用于归档触发）。

    ``role=thinking`` 按与 ``conversation_history_for_llm`` 相同的截断规则计长，
    使归档阈值与下游 LLM 上下文更一致。
    """
    from miniagent.memory.context import estimate_tokens

    n = 0
    for m in history:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        c = m.get("content")
        if role == "thinking":
            if isinstance(c, str) and c.strip():
                cap = _thinking_for_llm_max_chars()
                mapped = f"（思考过程）\n{_truncate_thinking_for_llm(c.strip(), cap)}"
                n += estimate_tokens(mapped)
                n += 5
            continue
        if isinstance(c, str):
            n += estimate_tokens(c)
        if role == "assistant" and m.get("tool_calls"):
            n += estimate_tokens(str(m.get("tool_calls")))
        n += 5
    return n
