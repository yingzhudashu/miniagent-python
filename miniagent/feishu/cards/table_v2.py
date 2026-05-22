"""互动卡片 schema 2.0 宽表（窄场景）。"""

from __future__ import annotations

from typing import Any

from miniagent.feishu.cards.gfm_table import extract_wide_gfm_table_rows


def extract_wide_gfm_table(
    text: str,
    *,
    max_pipes: int = 14,
) -> list[list[str]] | None:
    """若正文含超宽 GFM 管道表，返回行矩阵（含表头）；委托 ``gfm_table``。"""
    return extract_wide_gfm_table_rows(text, max_pipes=max_pipes)


def build_v2_table_card(
    rows: list[list[str]],
    *,
    header_title: str = "表格",
    template: str = "blue",
    max_rows: int = 20,
    max_cols: int = 8,
) -> dict[str, Any]:
    """构建 schema 2.0 单表卡片。"""
    if not rows:
        raise ValueError("empty table")
    ncols = min(max_cols, max(len(r) for r in rows))
    clipped = [r[:ncols] + [""] * (ncols - len(r[:ncols])) for r in rows[:max_rows]]
    headers = clipped[0]
    columns = [
        {
            "name": f"c{ci}",
            "display_name": (headers[ci] if ci < len(headers) else f"列{ci + 1}")[:40] or f"列{ci + 1}",
            "width": "auto",
        }
        for ci in range(ncols)
    ]
    body_rows: list[list[dict[str, Any]]] = []
    for ri, row in enumerate(clipped):
        if ri == 0:
            continue
        body_rows.append(
            [{"tag": "plain_text", "content": (row[ci] if ci < len(row) else "")[:500]} for ci in range(ncols)]
        )
    if not body_rows:
        body_rows = [
            [{"tag": "plain_text", "content": (headers[ci] if ci < len(headers) else "")[:500]} for ci in range(ncols)]
        ]
    return {
        "schema": "2.0",
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": (header_title or "表格")[:200]},
            "template": template or "blue",
        },
        "body": {
            "elements": [
                {
                    "tag": "table",
                    "page_size": min(len(body_rows), 10),
                    "row_height": "low",
                    "columns": columns,
                    "rows": body_rows,
                }
            ]
        },
    }


__all__ = ["build_v2_table_card", "extract_wide_gfm_table"]
