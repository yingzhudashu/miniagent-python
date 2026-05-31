"""Feishu — 共享辅助函数

消除 feishu_im_tools.py、feishu_doc_tools.py、feishu_bitable_tools.py 中的重复代码。
"""

from __future__ import annotations

import json
import os
from typing import Any


def resolve_under_workspace(path: str | None) -> str:
    """解析路径到工作空间目录下。

    Args:
        path: 相对路径或 None。

    Returns:
        工作空间下的绝对路径。
    """
    from miniagent.security.sandbox import get_default_workspace

    workspace = get_default_workspace()
    if path:
        return os.path.join(workspace, path)
    return workspace


def fmt_json(data: Any, indent: int = 2) -> str:
    """格式化 JSON 数据为字符串。

    Args:
        data: JSON 数据（dict、list 等）。
        indent: 缩进级别。

    Returns:
        格式化后的 JSON 字符串。
    """
    return json.dumps(data, indent=indent, ensure_ascii=False)