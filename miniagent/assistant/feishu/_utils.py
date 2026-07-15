"""Feishu — 共享辅助函数

消除 feishu_im_tools.py、feishu_doc_tools.py、feishu_bitable_tools.py 中的重复代码。
"""

from __future__ import annotations

import json
import os
from typing import Any


def resolve_under_workspace(workspace: str, rel: str) -> str:
    """将会话相对路径解析为实路径；越出 workspace 则抛 ValueError。

    Args:
        workspace: 工作空间根目录。
        rel: 相对路径。

    Returns:
        解析后的绝对路径。

    Raises:
        ValueError: 路径越出工作空间。
    """
    base = os.path.realpath(workspace)
    tail = (rel or "").strip().replace("\\", "/").lstrip("/")
    cand = os.path.realpath(os.path.join(base, tail))
    if cand != base and not cand.startswith(base + os.sep):
        raise ValueError("路径越出会话工作区")
    return cand


def fmt_json(data: Any, indent: int = 2) -> str:
    """格式化 JSON 数据为字符串。

    Args:
        data: JSON 数据（dict、list 等）。
        indent: 缩进级别。

    Returns:
        格式化后的 JSON 字符串。
    """
    return json.dumps(data, indent=indent, ensure_ascii=False)