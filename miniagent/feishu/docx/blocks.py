"""Feishu docx v1 block operations."""

from __future__ import annotations

import json
import logging
from typing import Any

from miniagent.core.constants import DOCX_APPEND_MAX_BLOCKS, DOCX_LIST_BLOCKS_MAX
from miniagent.feishu.lark_client import build_client
from miniagent.feishu.lark_response import format_lark_response_error
from miniagent.feishu.types import FeishuConfig

DOCX_APPEND_MAX_CHARS = 12_000
_TEXT_RUN_MAX = 1800
_BLOCK_PAGE = 1
_BLOCK_TEXT = 2


class DocxBlockCreateError(RuntimeError):
    """Feishu rejected rich Docx block creation before a safe fallback point."""

    def __init__(
        self,
        message: str,
        *,
        created_count: int = 0,
        batch_index: int = 0,
        block_types: list[int] | None = None,
    ):
        super().__init__(message)
        self.created_count = created_count
        self.batch_index = batch_index
        self.block_types = block_types or []


def _block_type_summary(blocks: list[Any]) -> list[int]:
    """Return only block_type values for diagnostics; never include payload/body text."""
    out: list[int] = []
    for block in blocks:
        raw = getattr(block, "block_type", None)
        if raw is None and isinstance(block, dict):
            raw = block.get("block_type")
        try:
            out.append(int(raw or 0))
        except (TypeError, ValueError):
            out.append(0)
    return out


def _chunk_runs(line: str) -> list[str]:
    """将长文本行切分为不超过 _TEXT_RUN_MAX 的片段（飞书 API 单次限制）。"""
    if not line:
        return ["\u200b"]
    parts: list[str] = []
    s = line
    while s:
        parts.append(s[:_TEXT_RUN_MAX])
        s = s[_TEXT_RUN_MAX:]
    return parts


def _paragraph_blocks_for_text(text: str) -> list[Any]:
    """将文本转换为飞书文档段落 Block 对象列表（按行分割，每行一个 Block）。"""
    from lark_oapi.api.docx.v1 import BlockBuilder, Text, TextElement, TextRun

    lines = text.split("\n") or [""]
    blocks = []
    for raw in lines[:DOCX_APPEND_MAX_BLOCKS]:
        runs = _chunk_runs(raw)
        elements = [
            TextElement.builder().text_run(TextRun.builder().content(r).build()).build()
            for r in runs
        ]
        blocks.append(
            BlockBuilder()
            .block_type(_BLOCK_TEXT)
            .text(Text.builder().elements(elements).build())
            .build()
        )
    return blocks


def _find_page_block_id(client, document_id: str) -> str:
    """查找文档的 Page Block ID（作为根容器用于追加内容）。"""
    from lark_oapi.api.docx.v1 import ListDocumentBlockRequest

    resp = client.docx.v1.document_block.list(
        ListDocumentBlockRequest.builder().document_id(document_id).page_size(50).build()
    )
    if not resp.success() or not resp.data or not resp.data.items:
        raise RuntimeError(
            f"Feishu list document blocks failed: {format_lark_response_error(resp)}"
        )
    for blk in resp.data.items:
        if int(getattr(blk, "block_type", 0) or 0) == _BLOCK_PAGE and getattr(
            blk, "block_id", None
        ):
            return str(blk.block_id)
    first = resp.data.items[0]
    if not getattr(first, "block_id", None):
        raise RuntimeError("Feishu list document blocks: empty block_id")
    return str(first.block_id)


def _count_children(client, document_id: str, page_block_id: str) -> int:
    """统计 Page Block 下的子 Block 数量（分页遍历）。"""
    from lark_oapi.api.docx.v1 import GetDocumentBlockChildrenRequest

    total = 0
    page_token = None
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
            raise RuntimeError(
                f"Feishu list block children failed: {format_lark_response_error(resp)}"
            )
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
    req = (
        CreateDocumentBlockChildrenRequest.builder()
        .document_id(document_id)
        .block_id(page_id)
        .request_body(body)
        .build()
    )
    resp = client.docx.v1.document_block_children.create(req)
    if not resp.success():
        raise RuntimeError(
            f"Feishu create block children failed: {format_lark_response_error(resp)}"
        )
    return len(children)


def _block_summary(blk: Any) -> dict:
    """提取 Block 的简要信息字典（block_id、block_type、parent_id）。"""
    return {
        "block_id": str(getattr(blk, "block_id", None) or ""),
        "block_type": int(getattr(blk, "block_type", None) or 0),
        "parent_id": str(getattr(blk, "parent_id", None) or ""),
    }


def list_document_blocks(
    config: FeishuConfig, document_id: str, *, page_token: str | None = None, page_size: int = 50
) -> tuple[list[dict], str | None, bool]:
    from lark_oapi.api.docx.v1 import ListDocumentBlockRequest

    client = build_client(config)
    b = ListDocumentBlockRequest.builder().document_id(document_id).page_size(min(page_size, 500))
    if page_token:
        b = b.page_token(page_token)
    resp = client.docx.v1.document_block.list(b.build())
    if not resp.success() or not resp.data:
        raise RuntimeError(f"Feishu list blocks failed: {format_lark_response_error(resp)}")
    items = [_block_summary(x) for x in (getattr(resp.data, "items", None) or [])]
    if len(items) > DOCX_LIST_BLOCKS_MAX:
        items = items[:DOCX_LIST_BLOCKS_MAX]
    nxt = getattr(resp.data, "page_token", None)
    return items, str(nxt) if nxt else None, bool(getattr(resp.data, "has_more", False))


def get_block(config: FeishuConfig, document_id: str, block_id: str) -> dict:
    from lark_oapi.api.docx.v1 import GetDocumentBlockRequest

    client = build_client(config)
    resp = client.docx.v1.document_block.get(
        GetDocumentBlockRequest.builder().document_id(document_id).block_id(block_id).build()
    )
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
    elements = [
        TextElement.builder().text_run(TextRun.builder().content(r).build()).build() for r in runs
    ]
    block = (
        BlockBuilder()
        .block_id(block_id)
        .block_type(_BLOCK_TEXT)
        .text(Text.builder().elements(elements).build())
        .build()
    )
    resp = client.docx.v1.document_block.patch(
        PatchDocumentBlockRequest.builder()
        .document_id(document_id)
        .block_id(block_id)
        .block(block)
        .build()
    )
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


def batch_update_blocks(
    config: FeishuConfig, document_id: str, requests_payload: list[dict]
) -> dict:
    from lark_oapi.api.docx.v1 import (
        BatchUpdateDocumentBlockRequest,
        BatchUpdateDocumentBlockRequestBody,
    )

    client = build_client(config)
    body = BatchUpdateDocumentBlockRequestBody.builder().requests(requests_payload).build()
    resp = client.docx.v1.document_block.batch_update(
        BatchUpdateDocumentBlockRequest.builder()
        .document_id(document_id)
        .request_body(body)
        .build()
    )
    if not resp.success():
        raise RuntimeError(f"Feishu batch_update failed: {format_lark_response_error(resp)}")
    return {"ok": True, "data": json.loads(resp.raw.content) if getattr(resp, "raw", None) else {}}


def append_markdown_to_document(
    config: FeishuConfig,
    document_id: str,
    markdown: str,
    *,
    use_renderer: bool = True,
    handle_images: bool = False,
    max_blocks: int = 30,
) -> tuple[int, list[str]]:
    """将 Markdown 内容追加到飞书文档（支持富文本渲染）。

    Args:
        config: 飞书配置
        document_id: 文档 ID
        markdown: Markdown 文本
        use_renderer: True 使用新渲染器（富文本），False 使用旧纯文本剥离
        handle_images: 是否处理图片（需要上传逻辑）
        max_blocks: 最大块数限制（默认 30）

    Returns:
        (成功追加的块数, 警告列表)

    Example:
        >>> n, warnings = append_markdown_to_document(cfg, doc_id, "# Title")
        >>> print(f"追加 {n} 个块，警告: {warnings}")

    Note:
        - 新渲染器支持：标题、粗体、斜体、链接、代码块、列表、引用、表格
        - 旧渲染器（use_renderer=False）仅剥离标记，输出纯文本
        - 表格块需要额外的 API 调用（使用 tables.py 的 create_table_with_values）
    """
    if not markdown or not markdown.strip():
        return 0, ["空内容"]

    if use_renderer:
        from miniagent.feishu.docx.markdown_renderer import (
            BlockType,
            build_lark_blocks_from_intermediate,
            markdown_to_feishu_blocks,
        )
        from miniagent.feishu.docx.tables import create_table_with_values

        # 1. 解析 Markdown 为中间表示
        result = markdown_to_feishu_blocks(markdown, max_blocks=max_blocks, handle_images=handle_images)

        # 2. 分离表格块（需要特殊处理）
        table_blocks = [b for b in result.blocks if b.block_type == BlockType.TABLE]
        non_table_blocks = [b for b in result.blocks if b.block_type != BlockType.TABLE]

        # 3. 处理表格（使用专门 API）
        table_count = 0
        table_failures = 0
        for tb in table_blocks:
            if tb.table_data and len(tb.table_data) > 0:
                try:
                    rows = len(tb.table_data)
                    cols = max(len(row) for row in tb.table_data) if rows > 0 else 0
                    if rows > 0 and cols > 0:
                        # 转换为纯文本（飞书表格不支持富文本样式）
                        values = []
                        for row in tb.table_data:
                            row_text = [run.content for run in row] if row else []
                            # 补齐列数
                            while len(row_text) < cols:
                                row_text.append("")
                            values.append(row_text)
                        create_table_with_values(
                            config, document_id,
                            row_size=rows, column_size=cols,
                            values=values,
                        )
                        table_count += 1
                except Exception as e:
                    table_failures += 1
                    result.warnings.append(f"rich table creation failed: {e}")

        # 4. 批量创建非表格块
        non_table_count = 0
        if non_table_blocks:
            try:
                lark_blocks = build_lark_blocks_from_intermediate(non_table_blocks)
                if lark_blocks:
                    non_table_count, block_warnings = _batch_create_blocks(
                        config, document_id, lark_blocks
                    )
                    result.warnings.extend(block_warnings)
            except Exception as e:
                result.warnings.append(f"rich block creation failed: {e}")
                try:
                    from miniagent.feishu.docx.markdown import markdown_to_plain_text
                    n = append_plain_text_to_document(config, document_id, markdown_to_plain_text(markdown))
                    return n, result.warnings + ["rich Markdown rendering failed; fallback to plain text"]
                except Exception as fallback_error:
                    return 0, result.warnings + [
                        f"rich Markdown rendering failed; plain-text fallback also failed: {fallback_error}"
                    ]

        total = non_table_count + table_count
        if total == 0 and table_failures:
            try:
                from miniagent.feishu.docx.markdown import markdown_to_plain_text
                n = append_plain_text_to_document(config, document_id, markdown_to_plain_text(markdown))
                return n, result.warnings + ["rich table creation failed; fallback to plain text"]
            except Exception as fallback_error:
                return 0, result.warnings + [
                    f"rich table creation failed; plain-text fallback also failed: {fallback_error}"
                ]
        return total, result.warnings

    else:
        # 向后兼容：使用旧实现
        from miniagent.feishu.docx.markdown import markdown_to_plain_text
        n = append_plain_text_to_document(config, document_id, markdown_to_plain_text(markdown))
        return n, []


def append_markdown_to_document_with_stats(
    config: FeishuConfig,
    document_id: str,
    markdown: str,
    *,
    use_renderer: bool = True,
    handle_images: bool = False,
    max_blocks: int = 30,
) -> tuple[int, list[str], dict[str, int]]:
    """Append Markdown and return metrics useful for tool diagnostics."""
    if use_renderer:
        try:
            from miniagent.feishu.docx.markdown_renderer import markdown_to_feishu_blocks

            preview = markdown_to_feishu_blocks(
                markdown,
                max_blocks=max_blocks,
                handle_images=handle_images,
            )
            stats = dict(preview.stats)
        except Exception:
            stats = {"total_blocks": 0}
    else:
        stats = {"total_blocks": 0}

    n, warnings = append_markdown_to_document(
        config,
        document_id,
        markdown,
        use_renderer=use_renderer,
        handle_images=handle_images,
        max_blocks=max_blocks,
    )
    stats["written_blocks"] = n
    stats["warnings"] = len(warnings)
    stats["fallback_count"] = sum(
        1 for w in warnings if "fallback" in w.lower() or "fell back" in w.lower()
    )
    return n, warnings, stats


def _batch_create_blocks(
    config: FeishuConfig,
    document_id: str,
    blocks: list[Any],
) -> tuple[int, list[str]]:
    """Batch-create rich Docx blocks and fail loudly before any block is written."""
    from lark_oapi.api.docx.v1 import (
        CreateDocumentBlockChildrenRequest,
        CreateDocumentBlockChildrenRequestBody,
    )

    if not blocks:
        return 0, []

    client = build_client(config)
    page_id = _find_page_block_id(client, document_id)
    idx = _count_children(client, document_id, page_id)

    max_per_request = DOCX_APPEND_MAX_BLOCKS
    total_created = 0
    warnings: list[str] = []

    for i in range(0, len(blocks), max_per_request):
        batch = blocks[i:i + max_per_request]
        batch_index = i // max_per_request
        block_types = _block_type_summary(batch)
        try:
            body = (
                CreateDocumentBlockChildrenRequestBody.builder()
                .children(batch)
                .index(idx + i)
                .build()
            )
            req = (
                CreateDocumentBlockChildrenRequest.builder()
                .document_id(document_id)
                .block_id(page_id)
                .request_body(body)
                .build()
            )
            resp = client.docx.v1.document_block_children.create(req)
            if resp.success():
                total_created += len(batch)
                continue
            err = format_lark_response_error(resp)
            _logger.warning("Feishu create block children failed: %s", err)
            if total_created == 0:
                raise DocxBlockCreateError(
                    (
                        f"Feishu create block children failed: {err}; "
                        f"batch_index={batch_index}; block_types={block_types}"
                    ),
                    created_count=0,
                    batch_index=batch_index,
                    block_types=block_types,
                )
            warnings.append(
                "rich block creation partially failed after "
                f"{total_created} blocks: {err}; batch_index={batch_index}; "
                f"block_types={block_types}"
            )
            break
        except Exception as e:
            if isinstance(e, DocxBlockCreateError):
                raise
            _logger.warning("Feishu create block children raised: %s", e)
            if total_created == 0:
                raise DocxBlockCreateError(
                    (
                        f"Feishu create block children raised: {e}; "
                        f"batch_index={batch_index}; block_types={block_types}"
                    ),
                    created_count=0,
                    batch_index=batch_index,
                    block_types=block_types,
                ) from e
            warnings.append(
                "rich block creation partially failed after "
                f"{total_created} blocks: {e}; batch_index={batch_index}; "
                f"block_types={block_types}"
            )
            break

    return total_created, warnings


_logger = logging.getLogger("miniagent.feishu.docx.blocks")
