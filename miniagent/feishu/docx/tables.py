"""飞书 docx 表格块操作（经 batch_update / block_children）。"""

from __future__ import annotations

from typing import Any

from miniagent.feishu.docx.blocks import _find_page_block_id, batch_update_blocks
from miniagent.feishu.lark_client import build_client
from miniagent.feishu.lark_response import format_lark_response_error
from miniagent.feishu.types import FeishuConfig

def create_table_block(
    config: FeishuConfig,
    document_id: str,
    *,
    row_size: int,
    column_size: int,
    parent_block_id: str | None = None,
    column_width: list[int] | None = None,
) -> str:
    """创建空表块，返回 table block_id。"""
    client = build_client(config)
    parent = parent_block_id or _find_page_block_id(client, document_id)
    body: dict[str, Any] = {
        "block_id": parent,
        "insert_table": {
            "row_size": max(1, row_size),
            "column_size": max(1, column_size),
        },
    }
    if column_width:
        body["insert_table"]["column_width"] = column_width
    out = batch_update_blocks(config, document_id, [body])
    data = out.get("data") or {}
    items = (data.get("data") or {}).get("blocks") or data.get("blocks") or []
    if items:
        bid = str(items[0].get("block_id") or items[0].get("table_id") or "")
        if bid:
            return bid
    raise RuntimeError(f"create_table: no block_id in response: {out!r}")


def write_table_cells(
    config: FeishuConfig,
    document_id: str,
    table_block_id: str,
    values: list[list[str]],
) -> None:
    """按二维数组写入表格单元格。"""
    if not values:
        return
    requests = [
        {
            "block_id": table_block_id,
            "update_table_cells": {
                "values": values,
            },
        }
    ]
    batch_update_blocks(config, document_id, requests)


def create_table_with_values(
    config: FeishuConfig,
    document_id: str,
    *,
    row_size: int,
    column_size: int,
    values: list[list[str]],
    parent_block_id: str | None = None,
    column_width: list[int] | None = None,
) -> str:
    tid = create_table_block(
        config,
        document_id,
        row_size=row_size,
        column_size=column_size,
        parent_block_id=parent_block_id,
        column_width=column_width,
    )
    write_table_cells(config, document_id, tid, values)
    return tid


__all__ = ["create_table_block", "create_table_with_values", "write_table_cells"]
