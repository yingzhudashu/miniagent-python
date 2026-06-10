"""GFM 管道表识别与解析（poll_server 与 CARD_V2 共用）。

性能优化：预编译表格分隔符正则，避免每次调用都重新编译。
"""

from __future__ import annotations

import re

# 性能优化：预编译正则表达式
_RE_GFM_SEPARATOR = re.compile(r"^\s*\|?[\s\-:|]+\|?\s*$")


def is_gfm_table_separator_line(line: str) -> bool:
    """检测是否为 GFM 表格分隔符行（如 ``| --- | --- |``）。

    使用预编译正则，比 ``re.match`` 每次调用更快。
    """
    return bool(_RE_GFM_SEPARATOR.match(line))


def parse_gfm_table_row_cells(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _rows_from_block_lines(block_lines: list[str]) -> list[list[str]]:
    """跳过空行与分隔符行，将表格块的每一行解析为单元格列表。"""
    rows: list[list[str]] = []
    for line in block_lines:
        if not line.strip() or is_gfm_table_separator_line(line):
            continue
        rows.append(parse_gfm_table_row_cells(line))
    return rows


def _is_table_block_head(lines: list[str], start: int) -> bool:
    """判断 ``start`` 处是否为 GFM 表头：当前行含管道符且下一行为分隔符行。"""
    if start + 1 >= len(lines):
        return False
    return "|" in lines[start] and is_gfm_table_separator_line(lines[start + 1])


def find_gfm_table_block(
    lines: list[str],
    start: int,
) -> tuple[int, int] | None:
    """从 ``start`` 起检测是否存在 GFM 表格块，返回 ``(header_start, block_end)``。

    不依赖管道符数量阈值，任何 GFM 管道符表格都检测。
    """
    if not _is_table_block_head(lines, start):
        return None
    j = start + 2
    while j < len(lines) and lines[j].strip() and "|" in lines[j]:
        j += 1
    return start, j


def find_wide_gfm_table_block(
    lines: list[str],
    start: int,
    *,
    max_pipes: int,
) -> tuple[int, int, int] | None:
    """从 ``start`` 起若存在宽 GFM 表，返回 ``(block_start, block_end, pipe_peak)``。"""
    if not _is_table_block_head(lines, start):
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
        rows = _rows_from_block_lines(lines[bi:bj])
        return rows if rows else None
    return None


def gfm_table_block_to_bullet_list(
    block_lines: list[str],
    *,
    key_value_threshold: int = 6,
) -> str:
    """将 GFM 管道表转为 bullet-point list 文本（供 lark_md 渲染）。

    列数 <= key_value_threshold 时用简洁格式：``- 值1 | 值2 | 值3``
    列数 > key_value_threshold 时用 key-value 格式：``- **表头1** → 值1, **表头2** → 值2``
    """
    rows = _rows_from_block_lines(block_lines)
    if not rows:
        return ""
    ncols = max(len(r) for r in rows)
    for r in rows:
        while len(r) < ncols:
            r.append("")
    headers = rows[0] if rows else []

    bullets: list[str] = []
    if ncols <= key_value_threshold:
        # 简洁格式：每行一个 bullet，用管道符分隔
        for row in rows[1:]:  # 跳过表头行
            cells = " | ".join(c.strip() for c in row if c is not None)
            bullets.append(f"- {cells}")
    else:
        # key-value 格式：每行数据以表头为键
        for row in rows[1:]:
            pairs: list[str] = []
            first_key = True
            for ci in range(ncols):
                key = (headers[ci] if ci < len(headers) else f"列{ci + 1}").strip()
                val = (row[ci] if ci < len(row) else "").strip()
                if first_key:
                    # 第一列作为 bullet 的标识
                    bullets.append(f"- **{key}** → {val}")
                    first_key = False
                else:
                    pairs.append(f"{key}={val}")
            if pairs:
                bullets[-1] += f"，{', '.join(pairs)}"
    return "\n".join(bullets)


def gfm_table_block_to_text_table(
    block_lines: list[str],
    *,
    max_cell_width: int = 28,
) -> str:
    """将 GFM 管道表转为等宽文本表（单元格截断）。"""
    rows = _rows_from_block_lines(block_lines)
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
            out_lines.append("|-" + "-|-".join("-" * widths[ci] for ci in range(ncols)) + "-|")
    return "\n".join(out_lines)


__all__ = [
    "extract_wide_gfm_table_rows",
    "find_gfm_table_block",
    "find_wide_gfm_table_block",
    "gfm_table_block_to_bullet_list",
    "gfm_table_block_to_text_table",
    "is_gfm_table_separator_line",
    "parse_gfm_table_row_cells",
]
