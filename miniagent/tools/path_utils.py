"""Tools — 共享路径解析辅助函数

消除 filesystem.py、vision.py、data_tools.py 中的重复代码。

重命名说明：从 _path_utils.py 重命名为 path_utils.py（规范化）。
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from miniagent.types.tool import ToolContext, ToolResult


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
        SandboxViolationError: 路径越界或不在允许目录中。
    """
    from miniagent.security.sandbox import resolve_sandbox_path

    p = path_str.strip()
    if not os.path.isabs(p):
        p = os.path.join(ctx.cwd, p)
    return resolve_sandbox_path(p, allowed_dirs_from_ctx(ctx))


def resolve_path_for_tool(path_str: str, ctx: ToolContext) -> tuple[str | None, ToolResult | None]:
    """解析路径；沙箱违规时返回 ``(None, ToolResult)`` 而非抛异常。

    供文件/数据等工具 handler 使用，与 ``feishu_utils.check_*`` 的返回风格一致。

    Args:
        path_str: 用户输入的路径（相对或绝对）。
        ctx: 工具执行上下文。

    Returns:
        (绝对路径, None): 解析成功。
        (None, ToolResult): 沙箱违规，已构造错误 ``ToolResult``。
    """
    from miniagent.types.error_prefix import ERROR_PREFIX
    from miniagent.types.errors import SandboxViolationError
    from miniagent.types.tool import ToolResult

    try:
        return resolve_path_from_ctx(path_str, ctx), None
    except SandboxViolationError as e:
        return None, ToolResult(success=False, content=f"{ERROR_PREFIX} {e}")


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
        SandboxViolationError: 路径越界或不在允许目录中。
    """
    from miniagent.security.sandbox import resolve_sandbox_path

    dirs = list(allowed) if allowed else allowed_dirs_simple(cwd)
    return resolve_sandbox_path(input_path, dirs)


__all__ = [
    "allowed_dirs_from_ctx",
    "resolve_path_from_ctx",
    "resolve_path_for_tool",
    "allowed_dirs_simple",
    "resolve_path_simple",
]
