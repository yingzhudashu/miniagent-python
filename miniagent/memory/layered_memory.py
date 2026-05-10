"""会话日记索引、会话级长期记忆、Agent 级长期记忆 — 按会话/全局 JSON 存储。"""

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


def _state_dir() -> str:
    return os.environ.get("MINI_AGENT_STATE", os.path.join(os.getcwd(), "workspaces"))


def _session_lt_path(session_key: str) -> str:
    d = os.path.join(_state_dir(), "memory", "session_lt")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{_safe_session_id(session_key)}.json")


def _agent_lt_path() -> str:
    d = os.path.join(_state_dir(), "memory", "agent_lt")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "global.json")


def load_session_longterm(session_key: str) -> dict[str, Any]:
    """会话级长期记忆：日摘要 + 指向日记文件的锚点。"""
    path = _session_lt_path(session_key)
    if not os.path.isfile(path):
        return {"session_key": session_key, "day_entries": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"session_key": session_key, "day_entries": []}


def save_session_longterm(session_key: str, data: dict[str, Any]) -> None:
    path = _session_lt_path(session_key)
    data = dict(data)
    data["session_key"] = session_key
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
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
    path = _agent_lt_path()
    if not os.path.isfile(path):
        return {"entries": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"entries": []}


def save_agent_longterm(data: dict[str, Any]) -> None:
    path = _agent_lt_path()
    data = dict(data)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
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


__all__ = [
    "load_session_longterm",
    "save_session_longterm",
    "append_session_day_rollup",
    "load_agent_longterm",
    "save_agent_longterm",
    "promote_to_agent_longterm",
]
