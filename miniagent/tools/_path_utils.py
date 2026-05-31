"""Tools — 共享路径解析辅助函数

消除 filesystem.py、vision.py、data_tools.py 中的重复代码。
"""

from __future__ import annotations

import os
from typing import Sequence


def allowed_dirs(cwd: str | None = None) -> list[str]:
    """返回允许访问的目录列表。

    Args:
        cwd: 当前工作目录，若为 None 则使用默认工作空间。

    Returns:
        允许访问的目录路径列表。
    """
    from miniagent.security.sandbox import get_default_workspace

    workspace = cwd or get_default_workspace()
    return [workspace]


def resolve_file_path(
    input_path: str,
    cwd: str | None = None,
    allowed: Sequence[str] | None = None,
) -> str:
    """解析并验证文件路径（沙箱保护）。

    Args:
        input_path: 用户输入的路径（相对或绝对）。
        cwd: 当前工作目录。
        allowed: 允许访问的目录列表，若为 None 则使用 allowed_dirs()。

    Returns:
        解析后的绝对路径。

    Raises:
        PermissionError: 路径越界或不在允许目录中。
        FileNotFoundError: 文件不存在（可选，由调用方决定）。
    """
    from miniagent.security.sandbox import resolve_sandbox_path

    dirs = allowed or allowed_dirs(cwd)
    return resolve_sandbox_path(input_path, dirs)