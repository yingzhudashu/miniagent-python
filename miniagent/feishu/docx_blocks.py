"""兼容重导出：请使用 ``miniagent.feishu.docx.blocks``。"""

from miniagent.feishu.docx.blocks import (
    DOCX_APPEND_MAX_CHARS,
    append_plain_text_to_document,
)

__all__ = ["DOCX_APPEND_MAX_CHARS", "append_plain_text_to_document"]
