"""会话历史 ↔ Chat Completions：磁盘可存扩展 ``role``（如 ``thinking``），调用 API 前做清洗。

``conversation_history_for_llm`` 将内部格式映射为 OpenAI 兼容的 ``user``/``assistant``/
``system``/``tool``；含归档标记的 system 消息会保留为简短衔接说明。

与 ``openai_message_sanitize`` 分工：后者剥离 ``_*`` 键，本模块处理角色与业务裁剪。
"""

from __future__ import annotations

from typing import Any

from miniagent.agent.problem_solver import strip_reflection_footer
from miniagent.agent.settings import get_config


def _thinking_for_llm_max_chars() -> int:
    """full 模式下注入 API 前允许保留的思考文本最大字符数。"""
    return max(0, get_config("memory.thinking_for_llm_max_chars", 10_000))


def _thinking_for_llm_compact_max_chars() -> int:
    """compact 模式下注入 API 前保留的思考摘要最大字符数。"""
    return max(0, get_config("memory.thinking_for_llm_compact_max_chars", 1_200))


def _thinking_for_llm_mode() -> str:
    """思考记录回灌给 LLM 的模式。

    - ``off``：不把内部 thinking 记录放入历史上下文。
    - ``compact``：默认模式，仅保留较短摘要，减少 history 波动和上下文膨胀。
    - ``full``：按 ``memory.thinking_for_llm_max_chars`` 保留更长正文。
    """
    mode = str(get_config("memory.thinking_for_llm_mode", "compact") or "").strip().lower()
    if mode not in {"off", "compact", "full"}:
        return "compact"
    return mode


def _truncate_thinking_for_llm(content: str, max_chars: int) -> str:
    """截断过长思考正文并附加提示，避免占满上下文窗口。"""
    if max_chars <= 0 or len(content) <= max_chars:
        return content
    tail = content[:max_chars].rstrip()
    note = "\n\n…（思考记录已截断供上下文窗口使用；完整内容见会话 history.json）"
    return tail + note


def _map_thinking_for_llm(content: str) -> str | None:
    """按配置将内部 thinking 记录映射为可发送给 LLM 的 assistant 文本。"""
    body = content.strip()
    if not body:
        return None
    mode = _thinking_for_llm_mode()
    if mode == "off":
        return None
    if mode == "full":
        capped = _truncate_thinking_for_llm(body, _thinking_for_llm_max_chars())
        return f"（思考过程）\n{capped}"
    capped = _truncate_thinking_for_llm(body, _thinking_for_llm_compact_max_chars())
    return f"（思考过程摘要）\n{capped}"


def conversation_history_for_llm(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """将持久化历史转为 Chat Completions 可接受的消息列表。

    处理规则：
    - role=thinking → 按 memory.thinking_for_llm_mode 映射或跳过（默认 compact）
    - role=system 且含 _history_archive_marker → 保留简短衔接说明
    - 去掉以下划线开头的元数据键（如 _timestamp）
    - 过滤非标准角色（仅保留 user/assistant/system/tool）

    Args:
        history: 持久化的会话历史消息列表（可能含 thinking、归档标记等）

    Returns:
        list[dict]: OpenAI Chat Completions API 兼容的消息列表

    Note:
        - thinking 消息会按 off/compact/full 模式处理
        - 归档标记的 system 消息仅保留 content 作为衔接说明
        - dict/list 类型值会做浅拷贝（避免原地修改）
    """
    out: list[dict[str, Any]] = []
    for m in history:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role == "thinking":
            c = m.get("content") or ""
            mapped = _map_thinking_for_llm(str(c))
            if mapped:
                out.append({"role": "assistant", "content": mapped})
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
        # 剥离 assistant 回复末尾的质量评估尾部（footer）：footer 是展示层内容，
        # 若回灌给 LLM 会被模型当作正文复述，叠加本轮新 footer 造成重复质量评估。
        # 此处同时清理历史中已被污染的旧 footer。落盘原文不变（仅影响 LLM 上下文）。
        content = clean.get("content")
        if clean.get("role") == "assistant" and isinstance(content, str):
            clean["content"] = strip_reflection_footer(content)
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
        - thinking 消息按 off/compact/full 映射后的长度计算
        - 每条消息额外加 5 tokens（role 开销）
        - tool_calls 按 JSON 字符串估算
    """
    from miniagent.agent.context import estimate_tokens

    n = 0
    for m in history:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        c = m.get("content")
        if role == "thinking":
            if isinstance(c, str):
                mapped = _map_thinking_for_llm(c)
                if not mapped:
                    continue
                n += estimate_tokens(mapped)
                n += 5
            continue
        if isinstance(c, str):
            n += estimate_tokens(c)
        if role == "assistant" and m.get("tool_calls"):
            n += estimate_tokens(str(m.get("tool_calls")))
        n += 5
    return n


def _message_token_estimate(message: dict[str, Any]) -> int:
    """单条 API 消息的 token 估算（与 estimate_history_messages_tokens 一致）。"""
    from miniagent.agent.context import estimate_tokens

    role = message.get("role")
    content = message.get("content")
    tokens = 5
    if role == "assistant" and message.get("tool_calls"):
        tokens += estimate_tokens(str(message.get("tool_calls")))
    if isinstance(content, str):
        tokens += estimate_tokens(content)
    return tokens


def format_history_for_llm(
    messages: list[dict[str, Any]],
    *,
    max_tokens: int | None = None,
) -> list[dict[str, Any]]:
    """将历史消息格式化为 LLM 输入，可选按 token 预算从头部裁剪。

    内部先调用 ``conversation_history_for_llm`` 做角色清洗与 thinking 映射。

    Args:
        messages: 持久化历史消息列表
        max_tokens: 最大 token 预算；超出时丢弃最旧消息直至满足预算

    Returns:
        list[dict]: OpenAI Chat Completions 兼容的消息列表
    """
    formatted = conversation_history_for_llm(messages)
    if max_tokens is None or max_tokens <= 0 or not formatted:
        return formatted

    token_counts = [_message_token_estimate(message) for message in formatted]
    total_tokens = sum(token_counts)
    first_kept = 0
    while first_kept < len(formatted) and total_tokens > max_tokens:
        total_tokens -= token_counts[first_kept]
        first_kept += 1
    return formatted[first_kept:]


__all__ = [
    "conversation_history_for_llm",
    "estimate_history_messages_tokens",
    "format_history_for_llm",
]
