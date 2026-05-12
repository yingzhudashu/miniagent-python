"""飞书 docx v1 块级写入最小封装：在文档页面块下追加纯文本段落。

使用开放平台 ``document_block_children.create``，**不是** ``document_block.batch_update``。
"""

from __future__ import annotations

from typing import Any

from miniagent.feishu.lark_response import format_lark_response_error
from miniagent.feishu.types import FeishuConfig

# 与开放平台 QPS/体量建议对齐的保守上限
DOCX_APPEND_MAX_CHARS = 12_000
DOCX_APPEND_MAX_BLOCKS = 30
_TEXT_RUN_MAX = 1800
# 飞书 docx block_type：页面=1，文本=2（见开放平台文档）
_BLOCK_PAGE = 1
_BLOCK_TEXT = 2


def _chunk_runs(line: str) -> list[str]:
    """将单行文本按飞书单次 text_run 上限切分为多段（空行用零宽占位）。"""
    if not line:
        return ["\u200b"]
    parts: list[str] = []
    s = line
    while s:
        parts.append(s[:_TEXT_RUN_MAX])
        s = s[_TEXT_RUN_MAX:]
    return parts


def _paragraph_blocks_for_text(text: str) -> list[Any]:
    """把纯文本拆成 docx 文本块列表（每行一个段落，受块数上限约束）。"""
    from lark_oapi.api.docx.v1 import Block, BlockBuilder, Text, TextElement, TextRun

    lines = text.split("\n")
    if not lines:
        lines = [""]
    blocks: list[Block] = []
    for raw in lines[:DOCX_APPEND_MAX_BLOCKS]:
        runs = _chunk_runs(raw)
        elements = [
            TextElement.builder().text_run(TextRun.builder().content(r).build()).build() for r in runs
        ]
        tb = Text.builder().elements(elements)
        blk = BlockBuilder().block_type(_BLOCK_TEXT).text(tb.build()).build()
        blocks.append(blk)
    return blocks


def _find_page_block_id(client, document_id: str) -> str:
    """列出文档根块，返回首个页面类型 block_id（无则退回首块）。"""
    from lark_oapi.api.docx.v1 import ListDocumentBlockRequest

    req = ListDocumentBlockRequest.builder().document_id(document_id).page_size(50).build()
    resp = client.docx.v1.document_block.list(req)
    if not resp.success() or not resp.data or not resp.data.items:
        raise RuntimeError(f"Feishu list document blocks failed: {format_lark_response_error(resp)}")
    for blk in resp.data.items:
        bt = getattr(blk, "block_type", None)
        bid = getattr(blk, "block_id", None)
        if bid is not None and int(bt or 0) == _BLOCK_PAGE:
            return str(bid)
    first = resp.data.items[0]
    bid0 = getattr(first, "block_id", None)
    if not bid0:
        raise RuntimeError("Feishu list document blocks: empty block_id")
    return str(bid0)


def _count_children(client, document_id: str, page_block_id: str) -> int:
    """分页累计某页面块下的子块数量（用于追加前统计）。"""
    from lark_oapi.api.docx.v1 import GetDocumentBlockChildrenRequest

    total = 0
    page_token: str | None = None
    while True:
        b = (
            GetDocumentBlockChildrenRequest.builder()
            .document_id(document_id)
            .block_id(page_block_id)
            .page_size(50)
        )
        if page_token:
            b = b.page_token(page_token)
        resp = client.docx.v1.document_block_children.get(b.build())
        if not resp.success() or not resp.data:
            raise RuntimeError(f"Feishu list block children failed: {format_lark_response_error(resp)}")
        items = getattr(resp.data, "items", None) or []
        total += len(items)
        if not getattr(resp.data, "has_more", False):
            break
        nxt = getattr(resp.data, "page_token", None)
        if not nxt or nxt == page_token:
            break
        page_token = str(nxt)
    return total


def append_plain_text_to_document(config: FeishuConfig, document_id: str, text: str) -> int:
    """在文档正文末尾追加纯文本（按换行拆成多个文本块）。

    Returns:
        追加的块数量。
    """
    import lark_oapi as lark
    from lark_oapi.api.docx.v1 import (
        CreateDocumentBlockChildrenRequest,
        CreateDocumentBlockChildrenRequestBody,
    )

    t = (text or "")[:DOCX_APPEND_MAX_CHARS]
    children = _paragraph_blocks_for_text(t)
    if not children:
        return 0

    client = lark.Client.builder().app_id(config.app_id).app_secret(config.app_secret).build()
    page_id = _find_page_block_id(client, document_id)
    insert_index = _count_children(client, document_id, page_id)
    body = CreateDocumentBlockChildrenRequestBody.builder().children(children).index(insert_index).build()
    req = (
        CreateDocumentBlockChildrenRequest.builder()
        .document_id(document_id)
        .block_id(page_id)
        .request_body(body)
        .build()
    )
    resp = client.docx.v1.document_block_children.create(req)
    if not resp.success():
        raise RuntimeError(f"Feishu create document block children failed: {format_lark_response_error(resp)}")
    return len(children)
