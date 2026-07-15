"""会话历史过长时，将最早若干完整轮次原样写入按会话隔离的日记并插入衔接锚点。

与 ``read_session_diary`` / ``search_session_diary`` 工具读路径一致；背景见 ``docs/MEMORY_SYSTEM.md``。

状态根目录统一由 ``infrastructure.paths.resolve_state_dir()`` 解析。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from miniagent.agent.constants import HISTORY_ARCHIVE_MAX_MESSAGES
from miniagent.agent.logging import get_logger
from miniagent.assistant.infrastructure.json_config import get_config
from miniagent.assistant.infrastructure.paths import resolve_state_dir as get_state_root
from miniagent.assistant.utils.session_id import safe_session_id

_logger = get_logger(__name__)


# 使用统一的 get_state_root() 函数获取状态根目录


def _diary_path(session_key: str, day: str) -> str:
    """某日日记 Markdown 绝对路径（确保目录存在）。"""
    base = os.path.join(get_state_root(), "memory", "diary", safe_session_id(session_key))
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, f"{day}.md")


def diary_file_path(session_key: str, day: str | None = None) -> str:
    """返回 ``memory/diary/<safe_session>/<day>.md`` 绝对路径。"""
    d = day or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return _diary_path(session_key, d)


def history_archive_max_messages() -> int:
    """归档消息数阈值（至少 1）；供渐进压缩等模块复用。"""
    return max(1, get_config("memory.history_max_messages", HISTORY_ARCHIVE_MAX_MESSAGES))


def history_archive_token_hint() -> int | None:
    """归档 token 提示阈值；未设置或无效时返回 None。"""
    v = get_config("memory.archive_token_hint", None)
    return v if v and v > 0 else None


def _max_messages() -> int:
    """内部别名：当前归档消息数阈值。"""
    return history_archive_max_messages()


def _max_tokens_hint() -> int | None:
    """内部别名：可选 token 估算阈值。"""
    return history_archive_token_hint()


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


def trim_history_tail_by_turns(history: list[dict[str, Any]], cap: int) -> bool:
    """从头部删除**至多一轮**（或一条首部非 user 消息），当 ``len(history) > cap`` 时执行。

    保留近期消息：通过反复由调用方调用本函数直至 ``len <= cap`` 实现渐进截断。

    Returns:
        若本次删除了至少一条消息则为 True，否则 False。
    """
    if cap < 0 or len(history) <= cap:
        return False
    if not history:
        return False
    top = history[0]
    role = top.get("role")
    if role != "user":
        history.pop(0)
        return True
    turn_len = _one_simple_turn_len(history, 0)
    if turn_len <= 0:
        history.pop(0)
        return True
    del history[:turn_len]
    return True


def append_archive_chunk_to_diary(
    session_key: str, chunk: list[dict[str, Any]]
) -> tuple[str, int, str] | None:
    """将一段消息 JSON 追加写入当日日记；成功返回 ``(path, seq, day)``，失败返回 None。"""
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
        return None
    return path, seq, day


def maybe_archive_old_turns(session_key: str, history: list[dict[str, Any]]) -> bool:
    """若历史超过阈值，从最旧 user 起**仅归档一轮**到日记并插入锚点；否则不操作。

    需仍低于阈值时由调用方多次调用（渐进式）。

    Returns:
        若完成一轮归档则为 True；未归档（未超阈值或失败回滚）为 False。
    """
    max_msg = _max_messages()
    tok_hint = _max_tokens_hint()
    from miniagent.agent.history import estimate_history_messages_tokens

    def over() -> bool:
        """是否超过条数或（若配置）近似 token 上限。"""
        if len(history) > max_msg:
            return True
        if tok_hint and estimate_history_messages_tokens(history) > tok_hint:
            return True
        return False

    if not over() or not history:
        return False

    fu = next(
        (i for i, m in enumerate(history) if m.get("role") == "user"),
        None,
    )
    if fu is None:
        if history:
            _logger.debug(
                "归档跳过：未找到 user 起点，丢弃 role=%s",
                history[0].get("role"),
            )
            history.pop(0)
            return True
        return False

    turn_len = _one_simple_turn_len(history, fu)
    if turn_len <= 0:
        history.pop(fu)
        return True

    chunk = history[fu : fu + turn_len]
    del history[fu : fu + turn_len]

    written = append_archive_chunk_to_diary(session_key, chunk)
    if written is None:
        history[fu:fu] = chunk
        return False

    path, seq, day = written
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
    return True


__all__ = [
    "maybe_archive_old_turns",
    "diary_file_path",
    "trim_history_tail_by_turns",
    "append_archive_chunk_to_diary",
    "history_archive_max_messages",
    "history_archive_token_hint",
]
