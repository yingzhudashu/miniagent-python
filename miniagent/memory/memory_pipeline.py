"""渐进式披露：在 **system** 侧拼接会话级 / Agent 级长期记忆摘要。

由 ``build_layered_memory_augmentation`` 生成可追加的文本；是否注入及长度上限受
``MINI_AGENT_LAYERED_MEMORY_*`` 环境变量控制。依赖 ``layered_memory`` 与按日 ``diary`` 文件。

披露顺序与隐私提示见 ``docs/MEMORY_SYSTEM.md``。
"""

from __future__ import annotations

import os

from miniagent.memory.layered_memory import load_agent_longterm, load_session_longterm


def _tail_diary_preview(session_key: str, max_chars: int = 2000) -> str:
    """加载该会话「今天」日记文件的前若干字符（若存在）。"""
    from datetime import datetime, timezone

    from miniagent.memory.history_archive import diary_file_path

    try:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = diary_file_path(session_key, day)
        if not os.path.isfile(path):
            return ""
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
        if len(raw) <= max_chars:
            return raw
        return raw[:max_chars] + "\n…(截断)"
    except OSError:
        return ""


def build_layered_memory_augmentation(
    session_key: str,
    *,
    user_input: str,
    include_diary_today: bool = True,
) -> str:
    """返回附加到 system prompt 的文本（不含跨会话 DefaultMemoryStore 部分）。"""
    flag = os.environ.get("MINI_AGENT_LAYERED_MEMORY_INJECT", "1").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return ""

    try:
        max_total = int(os.environ.get("MINI_AGENT_LAYERED_MEMORY_MAX_CHARS", "12000"))
    except ValueError:
        max_total = 12000

    parts: list[str] = []

    if include_diary_today:
        try:
            dc = int(os.environ.get("MINI_AGENT_DIARY_PREVIEW_CHARS", "2000"))
        except ValueError:
            dc = 2000
        prev = _tail_diary_preview(session_key, max_chars=max(0, dc))
        if prev.strip():
            parts.append("【本会话今日日记摘录（归档块可能含完整历史）】\n" + prev.strip())

    if os.environ.get("MINI_AGENT_LAYERED_MEMORY_SESSION_LT", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    ):
        slt = load_session_longterm(session_key)
        days = slt.get("day_entries") or []
        if days:
            lines = []
            for e in days[-20:]:
                lines.append(
                    f"- {e.get('day', '')}: {e.get('summary', '')} "
                    f"(日记: {e.get('diary_path', '')})"
                )
            parts.append("【会话长期记忆 — 日索引】\n" + "\n".join(lines))

    if os.environ.get("MINI_AGENT_LAYERED_MEMORY_AGENT_LT", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    ):
        ag = load_agent_longterm()
        entries = ag.get("entries") or []
        if entries:
            tail = entries[-15:]
            blob = "\n".join(
                f"- ({x.get('source_session', '')}) {x.get('text', '')[:400]}"
                for x in tail
            )
            parts.append("【Agent 长期记忆】\n" + blob)

    _ = user_input
    if not parts:
        return ""
    out = "\n\n".join(parts)
    if max_total > 0 and len(out) > max_total:
        return out[:max_total] + "\n…(layered_memory 总长度已截断)"
    return out


__all__ = ["build_layered_memory_augmentation"]
