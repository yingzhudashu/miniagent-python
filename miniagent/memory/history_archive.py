"""会话历史过长时，将最早若干完整轮次原样写入按会话隔离的日记并插入衔接锚点。"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any

from miniagent.infrastructure.logger import get_logger

_logger = get_logger(__name__)


def _safe_session_id(session_key: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", session_key)


def safe_session_id_for_memory(session_key: str) -> str:
    """与日记 / session_lt 文件名一致的 session_key 安全化（供其它模块调用）。"""
    return _safe_session_id(session_key)


def _state_dir() -> str:
    return os.environ.get("MINI_AGENT_STATE", os.path.join(os.getcwd(), "workspaces"))


def _diary_path(session_key: str, day: str) -> str:
    base = os.path.join(_state_dir(), "memory", "diary", _safe_session_id(session_key))
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, f"{day}.md")


def diary_file_path(session_key: str, day: str | None = None) -> str:
    """返回 ``memory/diary/<safe_session>/<day>.md`` 绝对路径。"""
    d = day or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return _diary_path(session_key, d)


def _max_messages() -> int:
    """长度阈值；环境变量可设较小值（测试或窄上下文），至少为 1。"""
    try:
        v = int(os.environ.get("MINI_AGENT_HISTORY_MAX_MESSAGES", "120"))
        return max(1, v)
    except ValueError:
        return 120


def _max_tokens_hint() -> int | None:
    raw = os.environ.get("MINI_AGENT_HISTORY_ARCHIVE_TOKEN_HINT", "").strip()
    if not raw:
        return None
    try:
        v = int(raw)
        return v if v > 0 else None
    except ValueError:
        return None


def _one_simple_turn_len(history: list[dict[str, Any]], start: int) -> int:
    """从 start（须为 user）起，到含 tool_calls 的 assistant 链结束，或到无 tool 的 assistant。"""
    n = len(history)
    if start >= n or history[start].get("role") != "user":
        return 0
    i = start + 1
    while i < n and history[i].get("role") == "thinking":
        i += 1
    if i >= n:
        return i - start
    if history[i].get("role") != "assistant":
        return i - start
    # assistant
    if history[i].get("tool_calls"):
        i += 1
        while i < n and history[i].get("role") == "tool":
            i += 1
        return i - start
    return i - start + 1


def trim_history_tail_by_turns(history: list[dict[str, Any]], cap: int) -> None:
    """从头部删除最旧消息直至 ``len(history) <= cap``，优先按「从首个 user 起的整轮」删除。"""
    guard = 0
    while len(history) > cap and guard < 10000:
        guard += 1
        if not history:
            break
        top = history[0]
        role = top.get("role")
        # 首部非 user：单条移除（锚点、孤儿等），避免半轮硬切
        if role != "user":
            history.pop(0)
            continue
        turn_len = _one_simple_turn_len(history, 0)
        if turn_len <= 0:
            history.pop(0)
            continue
        del history[:turn_len]


def maybe_archive_old_turns(session_key: str, history: list[dict[str, Any]]) -> None:
    """若历史超过阈值，从最旧端按轮剪切到日记文件，并插入锚点消息（不总结正文）。"""
    max_msg = _max_messages()
    tok_hint = _max_tokens_hint()
    from miniagent.memory.history_bridge import estimate_history_messages_tokens

    def over() -> bool:
        if len(history) > max_msg:
            return True
        if tok_hint and estimate_history_messages_tokens(history) > tok_hint:
            return True
        return False

    while over():
        if not history:
            break
        fu = next(
            (i for i, m in enumerate(history) if m.get("role") == "user"),
            None,
        )
        if fu is None:
            # 无 user 起点（例如连续锚点）：丢弃首部一条并继续，直至有 user 或可清空
            if history:
                _logger.debug(
                    "归档跳过：未找到 user 起点，丢弃 role=%s",
                    history[0].get("role"),
                )
                history.pop(0)
            continue
        turn_len = _one_simple_turn_len(history, fu)
        if turn_len <= 0:
            history.pop(fu)
            continue
        chunk = history[fu : fu + turn_len]
        del history[fu : fu + turn_len]

        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = diary_file_path(session_key, day)
        seq = int(datetime.now(timezone.utc).timestamp() * 1000) % 1_000_000_000
        header = f"\n\n## archive {day} seq={seq} session={session_key!r}\n\n"
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(header)
                f.write("```json\n")
                f.write(json.dumps(chunk, ensure_ascii=False, indent=2))
                f.write("\n```\n")
        except OSError as e:
            _logger.warning("写入会话日记失败: %s", e)
            # 放回去以免丢失
            history[:0] = chunk
            break

        anchor = (
            f"[历史已归档至日记 {path} ，片段 seq={seq} ，共 {turn_len} 条消息。"
            "需要细节时请检索该会话当日日记文件。]"
        )
        archive_ref: dict[str, Any] = {
            "diary_path": path,
            "day": day,
            "seq": seq,
            "message_count": turn_len,
            "session_key": session_key,
        }
        history.insert(
            fu,
            {
                "role": "system",
                "content": anchor,
                "_history_archive_marker": True,
                "_archive_ref": archive_ref,
            },
        )


__all__ = [
    "maybe_archive_old_turns",
    "diary_file_path",
    "trim_history_tail_by_turns",
    "safe_session_id_for_memory",
]
