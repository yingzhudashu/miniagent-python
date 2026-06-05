"""飞书云文档（Docx）块级操作封装。

提供文档创建、删除、获取、块列举、块文本更新等能力。
供 ``feishu_doc`` 工具及上层文档管理模块使用。
底层通过 lark-oapi SDK 与飞书开放平台交互。

新增功能（富文本渲染）：
- ``append_markdown_to_document``: 将 Markdown 内容转换为飞书文档块（支持标题、列表、代码块、表格等）
- 支持内联样式（粗体、斜体、链接、内联代码）
- 向后兼容：旧的 ``append_plain_text_to_document`` 仍可用
"""

from miniagent.feishu.docx.blocks import (
    DOCX_APPEND_MAX_BLOCKS,
    DOCX_APPEND_MAX_CHARS,
    append_markdown_to_document,
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
from miniagent.feishu.docx.markdown_renderer import (
    BlockType,
    FeishuBlock,
    MarkdownConversionResult,
    TextRun,
    TextStyle,
    build_lark_blocks_from_intermediate,
    markdown_to_feishu_blocks,
)

__all__ = [
    # 常量
    "DOCX_APPEND_MAX_BLOCKS",
    "DOCX_APPEND_MAX_CHARS",
    # 块操作（旧）
    "append_plain_text_to_document",
    # 块操作（新：富文本渲染）
    "append_markdown_to_document",
    # 其他块操作
    "batch_update_blocks",
    "create_document",
    "delete_block",
    "delete_document",
    "get_block",
    "get_document",
    "get_document_raw_content",
    "list_document_blocks",
    "update_block_text",
    # Markdown 渲染器（新）
    "BlockType",
    "FeishuBlock",
    "TextRun",
    "TextStyle",
    "MarkdownConversionResult",
    "markdown_to_feishu_blocks",
    "build_lark_blocks_from_intermediate",
]
