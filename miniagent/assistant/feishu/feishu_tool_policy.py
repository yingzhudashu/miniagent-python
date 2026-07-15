"""飞书扩展内置工具名集合（IM + 云文档 + 多维表格）。"""

from __future__ import annotations

from miniagent.assistant.tools.feishu_bitable_tools import FEISHU_BITABLE_TOOL_NAMES
from miniagent.assistant.tools.feishu_card_tools import FEISHU_CARD_TOOL_NAMES
from miniagent.assistant.tools.feishu_doc_tools import FEISHU_DOC_TOOL_NAMES
from miniagent.assistant.tools.feishu_im_tools import FEISHU_IM_TOOL_NAMES

FEISHU_EXT_TOOL_NAMES = frozenset(
    set(FEISHU_IM_TOOL_NAMES)
    | set(FEISHU_DOC_TOOL_NAMES)
    | set(FEISHU_BITABLE_TOOL_NAMES)
    | set(FEISHU_CARD_TOOL_NAMES)
)

__all__ = [
    "FEISHU_BITABLE_TOOL_NAMES",
    "FEISHU_CARD_TOOL_NAMES",
    "FEISHU_DOC_TOOL_NAMES",
    "FEISHU_EXT_TOOL_NAMES",
    "FEISHU_IM_TOOL_NAMES",
]
