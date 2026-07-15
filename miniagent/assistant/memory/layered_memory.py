"""分层长期记忆 JSON：会话 rollup（``session_lt``）与全局 Agent 摘要（``agent_lt``）。

与 ``history_archive`` 写出的按日 ``diary`` Markdown 相配合：本模块负责结构化锚点与
读写的稳定文件名（经 ``safe_session_id`` 净化 ``session_key``）。

Layer 3 摘要语义见 ``docs/MEMORY_SYSTEM.md``。

状态根目录统一由 ``infrastructure.paths.resolve_state_dir()`` 解析。
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from miniagent.agent.logging import get_logger
from miniagent.assistant.infrastructure.paths import resolve_state_dir as get_state_root
from miniagent.assistant.infrastructure.persistence import dump_state_file, load_state_file
from miniagent.assistant.infrastructure.state_schemas import install_builtin_state_schemas
from miniagent.assistant.utils.session_id import safe_session_id

_logger = get_logger(__name__)
install_builtin_state_schemas()


# 使用统一的 safe_session_id 函数
_safe_session_id = safe_session_id


# 使用统一的 get_state_root() 函数获取状态根目录


def _session_lt_path(session_key: str) -> str:
    """``memory/session_lt/<safe>.json`` 路径。"""
    d = os.path.join(get_state_root(), "memory", "session_lt")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{_safe_session_id(session_key)}.json")


def _agent_lt_path() -> str:
    """全局 Agent 长期记忆 ``memory/agent_lt/global.json`` 路径。"""
    d = os.path.join(get_state_root(), "memory", "agent_lt")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "global.json")


def load_session_longterm(session_key: str) -> dict[str, Any]:
    """会话级长期记忆：日摘要 + 指向日记文件的锚点。"""
    path = _session_lt_path(session_key)
    if not os.path.isfile(path):
        return {"session_key": session_key, "day_entries": []}
    try:
        return load_state_file("session_longterm", path)
    except Exception:
        return {"session_key": session_key, "day_entries": []}


def save_session_longterm(session_key: str, data: dict[str, Any]) -> None:
    """写入会话级长期记忆 JSON，并刷新 ``updated_at``。"""
    path = _session_lt_path(session_key)
    data = dict(data)
    data["session_key"] = session_key
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        dump_state_file("session_longterm", path, data)
    except OSError as e:
        _logger.warning("写入 session_lt 失败: %s", e)


def append_session_day_rollup(
    session_key: str,
    *,
    day: str,
    diary_relative: str,
    summary: str,
) -> None:
    """追加一条「某日日记」的目录式摘要（由调度器/精炼任务调用）。"""
    doc = load_session_longterm(session_key)
    entries: list[dict[str, Any]] = list(doc.get("day_entries") or [])
    entries.append(
        {
            "day": day,
            "diary_path": diary_relative,
            "summary": summary,
            "added_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    doc["day_entries"] = entries
    save_session_longterm(session_key, doc)


def load_agent_longterm() -> dict[str, Any]:
    """读取全局 Agent 长期记忆；缺省为 ``{"entries": []}``。"""
    path = _agent_lt_path()
    if not os.path.isfile(path):
        return {"entries": []}
    try:
        return load_state_file("agent_longterm", path)
    except Exception:
        return {"entries": []}


def save_agent_longterm(data: dict[str, Any]) -> None:
    """写入全局 Agent 长期记忆并刷新 ``updated_at``。"""
    path = _agent_lt_path()
    data = dict(data)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        dump_state_file("agent_longterm", path, data)
    except OSError as e:
        _logger.warning("写入 agent_lt 失败: %s", e)


def promote_to_agent_longterm(
    text: str,
    *,
    source_session: str,
    priority: int = 0,
) -> None:
    """将一条高价值文本写入 Agent 全局长期记忆。"""
    doc = load_agent_longterm()
    ent = list(doc.get("entries") or [])
    ent.append(
        {
            "text": text,
            "source_session": source_session,
            "priority": priority,
            "added_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    doc["entries"] = ent
    save_agent_longterm(doc)


def remove_agent_longterm_entries_for_session(source_session: str) -> int:
    """从全局 agent_lt 中移除指定来源会话的条目。

    Args:
        source_session: 来源 session_key（如 ``__bg__<task_id>``）

    Returns:
        移除的条目数量
    """
    if not source_session:
        return 0
    doc = load_agent_longterm()
    entries = list(doc.get("entries") or [])
    kept = [e for e in entries if e.get("source_session") != source_session]
    removed = len(entries) - len(kept)
    if removed:
        doc["entries"] = kept
        save_agent_longterm(doc)
    return removed


__all__ = [
    "load_session_longterm",
    "save_session_longterm",
    "append_session_day_rollup",
    "load_agent_longterm",
    "save_agent_longterm",
    "promote_to_agent_longterm",
    "remove_agent_longterm_entries_for_session",
]
