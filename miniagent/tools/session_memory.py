"""会话记忆只读工具：读取按会话隔离的归档日记、在日记目录内搜索关键词。

与 ``history_archive`` 写入布局一致；语义见 ``docs/MEMORY_SYSTEM.md``。

重构说明：使用 ToolBuilder 简化工具定义，并统一到 ALL_TOOLS。
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from miniagent.infrastructure.paths import resolve_state_dir
from miniagent.memory.history_archive import diary_file_path
from miniagent.tools.base import tool
from miniagent.types.tool import ToolContext, ToolDefinition, ToolResult
from miniagent.utils.session_id import safe_session_id

# ════════════════════════════════════════════════════════
# Handlers
# ════════════════════════════════════════════════════════


async def _read_session_diary_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """读取本会话某日 diary Markdown（可截断）。"""
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
        return ToolResult(success=False, content=f"未找到日记文件: {path}（日期 {d}）")

    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
    except OSError as e:
        return ToolResult(success=False, content=f"读取失败: {e}")

    if len(raw) > max_chars:
        raw = raw[:max_chars] + "\n…(已截断)"
    return ToolResult(success=True, content=raw, meta={"path": path})


def _diary_query_positions(text: str, query: str, max_hits: int) -> list[int]:
    """返回 query 在 text 中所有命中起始位置（至多 max_hits 处）。"""
    if not query or max_hits < 1:
        return []
    positions: list[int] = []
    start = 0
    step = max(1, len(query))
    while len(positions) < max_hits:
        pos = text.find(query, start)
        if pos < 0:
            break
        positions.append(pos)
        start = pos + step
    return positions


async def _search_session_diary_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """在会话 diary 目录内做子串扫描，返回带上下文的命中片段（每文件可多命中）。"""
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
    try:
        max_hits_per_file = int(args.get("max_hits_per_file", 5))
    except (TypeError, ValueError):
        max_hits_per_file = 5
    max_files = max(1, min(max_files, 100))
    ctx_chars = max(20, min(ctx_chars, 2000))
    max_hits_per_file = max(1, min(max_hits_per_file, 50))

    root = os.path.join(resolve_state_dir(), "memory", "diary", safe_session_id(sk))
    if not os.path.isdir(root):
        return ToolResult(success=True, content="（该会话尚无 diary 目录）")

    hits: list[str] = []
    files = sorted(f for f in os.listdir(root) if f.endswith(".md") or f.endswith(".txt"))
    for name in files[:max_files]:
        fp = os.path.join(root, name)
        try:
            with open(fp, encoding="utf-8") as f:
                text = f.read()
        except OSError:
            continue
        positions = _diary_query_positions(text, q, max_hits_per_file)
        if not positions:
            continue
        for idx, pos in enumerate(positions, start=1):
            lo = max(0, pos - ctx_chars)
            hi = min(len(text), pos + len(q) + ctx_chars)
            snippet = text[lo:hi].replace("\n", " ")
            label = f"--- {name} (#{idx}) ---" if len(positions) > 1 else f"--- {name} ---"
            hits.append(f"{label}\n…{snippet}…")

    if not hits:
        return ToolResult(success=True, content=f"未在日记目录中找到 {q!r}。")
    return ToolResult(success=True, content="\n\n".join(hits))


# ════════════════════════════════════════════════════════
# Tool Definitions (使用 ToolBuilder)
# toolbox=None（核心工具箱，始终可用）
# ════════════════════════════════════════════════════════

session_memory_tools: dict[str, ToolDefinition] = {
    "read_session_diary": tool("read_session_diary", "读取当前会话在 memory/diary 下某日的归档日记（Markdown 内嵌 JSON 块）。")
        .optional("day", "string", "YYYY-MM-DD，默认今天（UTC）")
        .optional("max_chars", "integer", "最大返回字符数，默认 8000")
        .sandbox()
        .core()  # 核心工具箱，始终可用
        .handler(_read_session_diary_handler)
        .build(),
    "search_session_diary": tool("search_session_diary", "在当前会话的 diary 目录下的文件中搜索子串，返回匹配片段（只读，每文件可返回多处命中）。")
        .param("query", "string", "搜索子串")
        .optional("max_files", "integer", "最多扫描文件数，默认 14")
        .optional("context_chars", "integer", "命中处前后各保留字符数，默认 120")
        .optional("max_hits_per_file", "integer", "每个文件最多返回命中数，默认 5")
        .sandbox()
        .core()  # 核心工具箱，始终可用
        .handler(_search_session_diary_handler)
        .build(),
}

__all__ = ["session_memory_tools"]
