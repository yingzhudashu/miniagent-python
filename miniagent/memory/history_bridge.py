"""会话历史 ↔ Chat Completions：磁盘可存扩展 ``role``（如 ``thinking``），调用 API 前做清洗。

``conversation_history_for_llm`` 将内部格式映射为 OpenAI 兼容的 ``user``/``assistant``/
``system``/``tool``；含归档标记的 system 消息会保留为简短衔接说明。

与 ``openai_message_sanitize`` 分工：后者剥离 ``_*`` 键，本模块处理角色与业务裁剪。
"""

from __future__ import annotations

from typing import Any

from miniagent.infrastructure.json_config import get_config


def _thinking_for_llm_max_chars() -> int:
    """注入 API 前允许保留的思考文本最大字符数。"""
    return max(0, get_config("feishu.card.thinking_max_chars", 10_000))


def _truncate_thinking_for_llm(content: str, max_chars: int) -> str:
    """截断过长思考正文并附加提示，避免占满上下文窗口。"""
    if max_chars <= 0 or len(content) <= max_chars:
        return content
    tail = content[:max_chars].rstrip()
    note = "\n\n…（思考记录已截断供上下文窗口使用；完整内容见会话 history.json）"
    return tail + note


def conversation_history_for_llm(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """将持久化历史转为 Chat Completions 可接受的消息列表。

    处理规则：
    - role=thinking → 合并为 assistant 文本块（标注为思考记录）
    - role=system 且含 _history_archive_marker → 保留简短衔接说明
    - 去掉以下划线开头的元数据键（如 _timestamp）
    - 过滤非标准角色（仅保留 user/assistant/system/tool）

    Args:
        history: 持久化的会话历史消息列表（可能含 thinking、归档标记等）

    Returns:
        list[dict]: OpenAI Chat Completions API 兼容的消息列表

    Note:
        - thinking 消息会被截断到 _thinking_for_llm_max_chars()
        - 归档标记的 system 消息仅保留 content 作为衔接说明
        - dict/list 类型值会做浅拷贝（避免原地修改）
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
                out.append({"role": "assistant", "content": f"（思考过程）\n{capped}"})
            continue
        if role == "system" and m.get("_history_archive_marker"):
            brief = (m.get("content") or "").strip()
            if brief:
                out.append({"role": "system", "content": brief})
            continue
        clean = {
            k: (v.copy() if isinstance(v, (dict, list)) else v)
            for k, v in m.items()
            if isinstance(k, str) and not k.startswith("_")
        }
        if clean.get("role") not in ("user", "assistant", "system", "tool"):
            continue
        out.append(clean)
    return out


def estimate_history_messages_tokens(history: list[dict[str, Any]]) -> int:
    """粗略估算历史消息的 token 数（用于归档触发阈值判断）。

    使用与 conversation_history_for_llm 相同的截断规则计长，
    使归档阈值与下游 LLM 上下文更一致。

    Args:
        history: 持久化的会话历史消息列表

    Returns:
        int: 估算的 token 总数

    Note:
        - thinking 消息按截断后的长度计算
        - 每条消息额外加 5 tokens（role 开销）
        - tool_calls 按 JSON 字符串估算
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
