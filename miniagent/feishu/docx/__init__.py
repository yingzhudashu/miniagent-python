"""飞书云文档（Docx）块级操作封装。

提供文档创建、删除、获取、块列举、块文本更新等能力。
供 ``feishu_doc`` 工具及上层文档管理模块使用。
底层通过 lark-oapi SDK 与飞书开放平台交互。
"""

from miniagent.feishu.docx.blocks import (
    DOCX_APPEND_MAX_BLOCKS,
    DOCX_APPEND_MAX_CHARS,
    append_plain_text_to_document,
    batch_update_blocks,
    delete_block,
    get_block,
    list_document_blocks,
    update_block_text,
)
from miniagent.feishu.docx.client import (
    create_document,
    delete_document,
    get_document,
    get_document_raw_content,
)

__all__ = [
    "DOCX_APPEND_MAX_BLOCKS",
    "DOCX_APPEND_MAX_CHARS",
    "append_plain_text_to_document",
    "batch_update_blocks",
    "create_document",
    "delete_block",
    "delete_document",
    "get_block",
    "get_document",
    "get_document_raw_content",
    "list_document_blocks",
    "update_block_text",
]
