"""GFM 管道表识别与解析（poll_server 与 CARD_V2 共用）。"""

from __future__ import annotations

import re
from typing import Any


def is_gfm_table_separator_line(line: str) -> bool:
    return bool(re.match(r"^\s*\|?[\s\-:|]+\|?\s*$", line))


def parse_gfm_table_row_cells(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def find_wide_gfm_table_block(
    lines: list[str],
    start: int,
    *,
    max_pipes: int,
) -> tuple[int, int, int] | None:
    """从 ``start`` 起若存在宽 GFM 表，返回 ``(block_start, block_end, pipe_peak)``。"""
    if start + 1 >= len(lines):
        return None
    row0 = lines[start]
    if "|" not in row0 or not is_gfm_table_separator_line(lines[start + 1]):
        return None
    j = start
    pipe_peak = 0
    while j < len(lines) and lines[j].strip() and "|" in lines[j]:
        pipe_peak = max(pipe_peak, lines[j].count("|"))
        j += 1
    if pipe_peak <= max_pipes:
        return None
    return start, j, pipe_peak


def extract_wide_gfm_table_rows(
    text: str,
    *,
    max_pipes: int = 14,
) -> list[list[str]] | None:
    """若正文含超宽 GFM 管道表，返回行矩阵（含表头行）。"""
    lines = (text or "").split("\n")
    i = 0
    while i < len(lines):
        found = find_wide_gfm_table_block(lines, i, max_pipes=max_pipes)
        if found is None:
            i += 1
            continue
        bi, bj, _ = found
        rows: list[list[str]] = []
        for line in lines[bi:bj]:
            if not line.strip() or is_gfm_table_separator_line(line):
                continue
            rows.append(parse_gfm_table_row_cells(line))
        return rows if rows else None
    return None


def gfm_table_block_to_text_table(
    block_lines: list[str],
    *,
    max_cell_width: int = 28,
) -> str:
    """将 GFM 管道表转为等宽文本表（单元格截断）。"""
    rows: list[list[str]] = []
    for line in block_lines:
        if not line.strip() or is_gfm_table_separator_line(line):
            continue
        rows.append(parse_gfm_table_row_cells(line))
    if not rows:
        return ""
    ncols = max(len(r) for r in rows)
    for r in rows:
        while len(r) < ncols:
            r.append("")
    widths: list[int] = []
    for ci in range(ncols):
        mw = 0
        for r in rows:
            if ci < len(r):
                mw = max(mw, len((r[ci] or "").replace("\n", " ").replace("\r", "")))
        widths.append(min(max_cell_width, max(mw, 3)))

    def trunc(cell: str, w: int) -> str:
        x = (cell or "").replace("\n", " ").replace("\r", "")
        if len(x) <= w:
            return x.ljust(w)
        if w <= 1:
            return "…"[:w]
        return x[: w - 1] + "…"

    out_lines: list[str] = []
    for ri, r in enumerate(rows):
        cells = [trunc(r[ci], widths[ci]) for ci in range(ncols)]
        out_lines.append("| " + " | ".join(cells) + " |")
        if ri == 0:
            out_lines.append(
                "|-" + "-|-".join("-" * widths[ci] for ci in range(ncols)) + "-|"
            )
    return "\n".join(out_lines)


__all__ = [
    "extract_wide_gfm_table_rows",
    "find_wide_gfm_table_block",
    "gfm_table_block_to_text_table",
    "is_gfm_table_separator_line",
    "parse_gfm_table_row_cells",
]
