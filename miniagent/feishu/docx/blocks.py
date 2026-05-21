"""Feishu docx v1 block operations."""
from __future__ import annotations

import json
from typing import Any

from miniagent.feishu.lark_client import build_client
from miniagent.feishu.lark_response import format_lark_response_error
from miniagent.feishu.types import FeishuConfig

DOCX_APPEND_MAX_CHARS = 12_000
DOCX_APPEND_MAX_BLOCKS = 30
_TEXT_RUN_MAX = 1800
_BLOCK_PAGE = 1
_BLOCK_TEXT = 2
_LIST_BLOCKS_MAX = 200

def _chunk_runs(line: str) -> list[str]:
    if not line:
        return ["\u200b"]
    parts: list[str] = []
    s = line
    while s:
        parts.append(s[:_TEXT_RUN_MAX])
        s = s[_TEXT_RUN_MAX:]
    return parts

def _paragraph_blocks_for_text(text: str) -> list[Any]:
    from lark_oapi.api.docx.v1 import BlockBuilder, Text, TextElement, TextRun
    lines = text.split("\n") or [""]
    blocks = []
    for raw in lines[:DOCX_APPEND_MAX_BLOCKS]:
        runs = _chunk_runs(raw)
        elements = [TextElement.builder().text_run(TextRun.builder().content(r).build()).build() for r in runs]
        blocks.append(BlockBuilder().block_type(_BLOCK_TEXT).text(Text.builder().elements(elements).build()).build())
    return blocks

def _find_page_block_id(client, document_id: str) -> str:
    from lark_oapi.api.docx.v1 import ListDocumentBlockRequest
    resp = client.docx.v1.document_block.list(ListDocumentBlockRequest.builder().document_id(document_id).page_size(50).build())
    if not resp.success() or not resp.data or not resp.data.items:
        raise RuntimeError(f"Feishu list document blocks failed: {format_lark_response_error(resp)}")
    for blk in resp.data.items:
        if int(getattr(blk, "block_type", 0) or 0) == _BLOCK_PAGE and getattr(blk, "block_id", None):
            return str(blk.block_id)
    first = resp.data.items[0]
    if not getattr(first, "block_id", None):
        raise RuntimeError("Feishu list document blocks: empty block_id")
    return str(first.block_id)

def _count_children(client, document_id: str, page_block_id: str) -> int:
    from lark_oapi.api.docx.v1 import GetDocumentBlockChildrenRequest
    total = 0
    page_token = None
    while True:
        b = GetDocumentBlockChildrenRequest.builder().document_id(document_id).block_id(page_block_id).page_size(50)
        if page_token:
            b = b.page_token(page_token)
        resp = client.docx.v1.document_block_children.get(b.build())
        if not resp.success() or not resp.data:
            raise RuntimeError(f"Feishu list block children failed: {format_lark_response_error(resp)}")
        total += len(getattr(resp.data, "items", None) or [])
        if not getattr(resp.data, "has_more", False):
            break
        nxt = getattr(resp.data, "page_token", None)
        if not nxt or nxt == page_token:
            break
        page_token = str(nxt)
    return total

def append_plain_text_to_document(config: FeishuConfig, document_id: str, text: str) -> int:
    from lark_oapi.api.docx.v1 import (
        CreateDocumentBlockChildrenRequest,
        CreateDocumentBlockChildrenRequestBody,
    )
    client = build_client(config)
    children = _paragraph_blocks_for_text((text or "")[:DOCX_APPEND_MAX_CHARS])
    if not children:
        return 0
    page_id = _find_page_block_id(client, document_id)
    idx = _count_children(client, document_id, page_id)
    body = CreateDocumentBlockChildrenRequestBody.builder().children(children).index(idx).build()
    req = CreateDocumentBlockChildrenRequest.builder().document_id(document_id).block_id(page_id).request_body(body).build()
    resp = client.docx.v1.document_block_children.create(req)
    if not resp.success():
        raise RuntimeError(f"Feishu create block children failed: {format_lark_response_error(resp)}")
    return len(children)

def _block_summary(blk: Any) -> dict:
    return {
        "block_id": str(getattr(blk, "block_id", None) or ""),
        "block_type": int(getattr(blk, "block_type", None) or 0),
        "parent_id": str(getattr(blk, "parent_id", None) or ""),
    }

def list_document_blocks(config: FeishuConfig, document_id: str, *, page_token: str | None = None, page_size: int = 50) -> tuple[list[dict], str | None, bool]:
    from lark_oapi.api.docx.v1 import ListDocumentBlockRequest
    client = build_client(config)
    b = ListDocumentBlockRequest.builder().document_id(document_id).page_size(min(page_size, 500))
    if page_token:
        b = b.page_token(page_token)
    resp = client.docx.v1.document_block.list(b.build())
    if not resp.success() or not resp.data:
        raise RuntimeError(f"Feishu list blocks failed: {format_lark_response_error(resp)}")
    items = [_block_summary(x) for x in (getattr(resp.data, "items", None) or [])]
    if len(items) > _LIST_BLOCKS_MAX:
        items = items[:_LIST_BLOCKS_MAX]
    nxt = getattr(resp.data, "page_token", None)
    return items, str(nxt) if nxt else None, bool(getattr(resp.data, "has_more", False))

def get_block(config: FeishuConfig, document_id: str, block_id: str) -> dict:
    from lark_oapi.api.docx.v1 import GetDocumentBlockRequest
    client = build_client(config)
    resp = client.docx.v1.document_block.get(GetDocumentBlockRequest.builder().document_id(document_id).block_id(block_id).build())
    if not resp.success() or not resp.data or not resp.data.block:
        raise RuntimeError(f"Feishu get block failed: {format_lark_response_error(resp)}")
    blk = resp.data.block
    out = _block_summary(blk)
    text = getattr(getattr(blk, "text", None), "elements", None)
    if text:
        parts = []
        for el in text:
            tr = getattr(el, "text_run", None)
            if tr and getattr(tr, "content", None):
                parts.append(str(tr.content))
        out["text"] = "".join(parts)
    return out

def update_block_text(config: FeishuConfig, document_id: str, block_id: str, content: str) -> None:
    from lark_oapi.api.docx.v1 import (
        BlockBuilder,
        PatchDocumentBlockRequest,
        Text,
        TextElement,
        TextRun,
    )
    client = build_client(config)
    runs = _chunk_runs(content)
    elements = [TextElement.builder().text_run(TextRun.builder().content(r).build()).build() for r in runs]
    block = BlockBuilder().block_id(block_id).block_type(_BLOCK_TEXT).text(Text.builder().elements(elements).build()).build()
    resp = client.docx.v1.document_block.patch(PatchDocumentBlockRequest.builder().document_id(document_id).block_id(block_id).block(block).build())
    if not resp.success():
        raise RuntimeError(f"Feishu patch block failed: {format_lark_response_error(resp)}")

def delete_block(config: FeishuConfig, document_id: str, block_id: str) -> None:
    batch_update_blocks(config, document_id, [{"block_id": block_id, "delete_block": {}}])


def clear_document_content_blocks(config: FeishuConfig, document_id: str) -> tuple[int, int]:
    """删除除页面块外的顶层子块（为 write replace 准备）。返回 (成功数, 失败数)。"""
    client = build_client(config)
    page_id = _find_page_block_id(client, document_id)
    ok_n = 0
    fail_n = 0
    items, _, _ = list_document_blocks(config, document_id, page_size=200)
    for b in items:
        bid = str(b.get("block_id") or "")
        if not bid or bid == page_id:
            continue
        bt = int(b.get("block_type") or 0)
        if bt == _BLOCK_PAGE:
            continue
        try:
            delete_block(config, document_id, bid)
            ok_n += 1
        except Exception:
            fail_n += 1
    return ok_n, fail_n


def batch_update_blocks(config: FeishuConfig, document_id: str, requests_payload: list[dict]) -> dict:
    from lark_oapi.api.docx.v1 import (
        BatchUpdateDocumentBlockRequest,
        BatchUpdateDocumentBlockRequestBody,
    )
    client = build_client(config)
    body = BatchUpdateDocumentBlockRequestBody.builder().requests(requests_payload).build()
    resp = client.docx.v1.document_block.batch_update(
        BatchUpdateDocumentBlockRequest.builder().document_id(document_id).request_body(body).build()
    )
    if not resp.success():
        raise RuntimeError(f"Feishu batch_update failed: {format_lark_response_error(resp)}")
    return {"ok": True, "data": json.loads(resp.raw.content) if getattr(resp, "raw", None) else {}}
