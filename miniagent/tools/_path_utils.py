"""Tools — 共享路径解析辅助函数

消除 filesystem.py、vision.py、data_tools.py 中的重复代码。
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from miniagent.types.tool import ToolContext


def allowed_dirs_from_ctx(ctx: ToolContext) -> list[str]:
    """从 ToolContext 获取允许访问的目录列表。

    优先使用 ctx.allowed_paths，未设置则回退到默认工作空间。

    Args:
        ctx: 工具执行上下文。

    Returns:
        允许访问的目录路径列表。
    """
    from miniagent.security.sandbox import get_default_workspace

    return ctx.allowed_paths if ctx.allowed_paths else [get_default_workspace()]


def resolve_path_from_ctx(path_str: str, ctx: ToolContext) -> str:
    """将路径解析为沙箱允许范围内的绝对路径。

    相对路径相对于 ctx.cwd（即会话 files/ 目录）解析，
    而非进程当前工作目录。

    Args:
        path_str: 用户输入的路径（相对或绝对）。
        ctx: 工具执行上下文。

    Returns:
        解析后的绝对路径。

    Raises:
        PermissionError: 路径越界或不在允许目录中。
    """
    from miniagent.security.sandbox import resolve_sandbox_path

    p = path_str.strip()
    if not os.path.isabs(p):
        p = os.path.join(ctx.cwd, p)
    return resolve_sandbox_path(p, allowed_dirs_from_ctx(ctx))


# 简化版本（不依赖 ToolContext）
def allowed_dirs_simple(cwd: str | None = None) -> list[str]:
    """返回允许访问的目录列表（简化版本）。

    Args:
        cwd: 当前工作目录，若为 None 则使用默认工作空间。

    Returns:
        允许访问的目录路径列表。
    """
    from miniagent.security.sandbox import get_default_workspace

    workspace = cwd or get_default_workspace()
    return [workspace]


def resolve_path_simple(
    input_path: str,
    cwd: str | None = None,
    allowed: Sequence[str] | None = None,
) -> str:
    """解析并验证文件路径（简化版本，不依赖 ToolContext）。

    Args:
        input_path: 用户输入的路径（相对或绝对）。
        cwd: 当前工作目录。
        allowed: 允许访问的目录列表，若为 None 则使用 allowed_dirs_simple()。

    Returns:
        解析后的绝对路径。

    Raises:
        PermissionError: 路径越界或不在允许目录中。
    """
    from miniagent.security.sandbox import resolve_sandbox_path

    dirs = allowed or allowed_dirs_simple(cwd)
    return resolve_sandbox_path(input_path, dirs)