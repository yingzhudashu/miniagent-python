"""会话记忆只读工具：读取按会话隔离的归档日记、在日记目录内搜索关键词。

与 ``history_archive`` 写入布局一致；语义见 ``docs/MEMORY_SYSTEM.md``。
"""

from __future__ import annotations

import os
from typing import Any

from miniagent.types.tool import ToolDefinition, ToolContext, ToolResult


_read_session_diary_schema = {
    "type": "function",
    "function": {
        "name": "read_session_diary",
        "description": "读取当前会话在 memory/diary 下某日的归档日记（Markdown 内嵌 JSON 块）。",
        "parameters": {
            "type": "object",
            "properties": {
                "day": {
                    "type": "string",
                    "description": "YYYY-MM-DD，默认今天（UTC）",
                },
                "max_chars": {
                    "type": "integer",
                    "description": "最大返回字符数，默认 8000",
                },
            },
            "required": [],
        },
    },
}


_search_session_diary_schema = {
    "type": "function",
    "function": {
        "name": "search_session_diary",
        "description": "在当前会话的 diary 目录下的文件中搜索子串，返回匹配片段（只读）。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索子串"},
                "max_files": {
                    "type": "integer",
                    "description": "最多扫描文件数，默认 14",
                },
                "context_chars": {
                    "type": "integer",
                    "description": "命中处前后各保留字符数，默认 120",
                },
            },
            "required": ["query"],
        },
    },
}


async def _read_session_diary_handler(
    args: dict[str, Any], ctx: ToolContext
) -> ToolResult:
    """读取本会话某日 ``diary`` Markdown（可截断）。"""
    from datetime import datetime, timezone

    from miniagent.memory.history_archive import diary_file_path

    sk = (ctx.session_key or "").strip()
    if not sk:
        return ToolResult(success=False, content="当前无 session_key，无法定位会话日记。")
    day = str(args.get("day") or "").strip() or None
    try:
        max_chars = int(args.get("max_chars", 8000))
    except (TypeError, ValueError):
        max_chars = 8000
    max_chars = max(100, min(max_chars, 100_000))

    path = diary_file_path(sk, day)
    if not os.path.isfile(path):
        d = day or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return ToolResult(
            success=False,
            content=f"未找到日记文件: {path}（日期 {d}）",
        )
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
    except OSError as e:
        return ToolResult(success=False, content=f"读取失败: {e}")
    if len(raw) > max_chars:
        raw = raw[:max_chars] + "\n…(已截断)"
    return ToolResult(success=True, content=raw, meta={"path": path})


async def _search_session_diary_handler(
    args: dict[str, Any], ctx: ToolContext
) -> ToolResult:
    """在会话 diary 目录内做子串扫描，返回带上下文的命中片段。"""
    from miniagent.memory.history_archive import safe_session_id_for_memory

    sk = (ctx.session_key or "").strip()
    q = str(args.get("query") or "")
    if not sk:
        return ToolResult(success=False, content="当前无 session_key。")
    if not q:
        return ToolResult(success=False, content="query 不能为空。")

    try:
        max_files = int(args.get("max_files", 14))
    except (TypeError, ValueError):
        max_files = 14
    try:
        ctx_chars = int(args.get("context_chars", 120))
    except (TypeError, ValueError):
        ctx_chars = 120
    max_files = max(1, min(max_files, 100))
    ctx_chars = max(20, min(ctx_chars, 2000))

    root = os.path.join(
        os.environ.get("MINI_AGENT_STATE", os.path.join(os.getcwd(), "workspaces")),
        "memory",
        "diary",
        safe_session_id_for_memory(sk),
    )
    if not os.path.isdir(root):
        return ToolResult(success=True, content="（该会话尚无 diary 目录）")

    hits: list[str] = []
    files = sorted(
        f for f in os.listdir(root) if f.endswith(".md") or f.endswith(".txt")
    )
    for name in files[:max_files]:
        fp = os.path.join(root, name)
        try:
            with open(fp, encoding="utf-8") as f:
                text = f.read()
        except OSError:
            continue
        pos = text.find(q)
        if pos < 0:
            continue
        lo = max(0, pos - ctx_chars)
        hi = min(len(text), pos + len(q) + ctx_chars)
        snippet = text[lo:hi].replace("\n", " ")
        hits.append(f"--- {name} ---\n…{snippet}…")

    if not hits:
        return ToolResult(success=True, content=f"未在日记目录中找到 {q!r}。")
    return ToolResult(success=True, content="\n\n".join(hits))


session_memory_tools: dict[str, ToolDefinition] = {
    "read_session_diary": ToolDefinition(
        schema=_read_session_diary_schema,
        handler=_read_session_diary_handler,
        permission="sandbox",
        help_text="只读：当前会话归档日记",
        toolbox=None,
    ),
    "search_session_diary": ToolDefinition(
        schema=_search_session_diary_schema,
        handler=_search_session_diary_handler,
        permission="sandbox",
        help_text="只读：在会话 diary 目录内搜索",
        toolbox=None,
    ),
}

__all__ = ["session_memory_tools"]
