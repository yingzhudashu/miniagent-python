"""只读 Git 工具：状态与差异（工作目录默认为会话 ``cwd``）。

不向仓库写入；大量 diff 截断见模块内 ``_MAX_DIFF_CHARS``。
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from miniagent.types.tool import ToolDefinition, ToolContext, ToolResult

_MAX_DIFF_CHARS = 48_000

_git_status_schema = {
    "type": "function",
    "function": {
        "name": "git_status",
        "description": "在指定目录执行 git status --porcelain=v1 -b，查看分支与工作区状态",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "仓库根目录，默认使用当前工作目录",
                },
            },
        },
    },
}

_git_diff_schema = {
    "type": "function",
    "function": {
        "name": "git_diff",
        "description": "查看 git diff（默认工作区相对 HEAD 的改动）",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "仓库根目录，默认使用当前工作目录",
                },
                "staged": {
                    "type": "boolean",
                    "description": "为 true 时对比暂存区（git diff --cached）",
                },
            },
        },
    },
}


def _resolve_repo_root(arg_path: str | None, ctx: ToolContext) -> str:
    base = (arg_path or "").strip() or ctx.cwd
    return os.path.abspath(base)


async def _git_status_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    root = _resolve_repo_root(args.get("path"), ctx)
    proc = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        root,
        "status",
        "--porcelain=v1",
        "-b",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out_b, err_b = await proc.communicate()
    out = (out_b or b"").decode("utf-8", errors="replace")
    err = (err_b or b"").decode("utf-8", errors="replace")
    if proc.returncode != 0:
        return ToolResult(success=False, content=f"git_status 失败（{root}）:\n{err or out}")
    return ToolResult(success=True, content=out.strip() or "（干净工作区）")


async def _git_diff_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    root = _resolve_repo_root(args.get("path"), ctx)
    staged = bool(args.get("staged"))
    cmd = ["git", "-C", root, "diff"]
    if staged:
        cmd.append("--cached")
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out_b, err_b = await proc.communicate()
    out = (out_b or b"").decode("utf-8", errors="replace")
    err = (err_b or b"").decode("utf-8", errors="replace")
    if proc.returncode != 0:
        return ToolResult(success=False, content=f"git_diff 失败（{root}）:\n{err or out}")
    if len(out) > _MAX_DIFF_CHARS:
        out = out[:_MAX_DIFF_CHARS] + f"\n\n… 已截断（>{_MAX_DIFF_CHARS} 字符）"
    return ToolResult(success=True, content=out.strip() or "（无差异）")


git_readonly_tools: dict[str, ToolDefinition] = {
    "git_status": ToolDefinition(
        schema=_git_status_schema,
        handler=_git_status_handler,
        permission="allowlist",
        help_text="git status",
        toolbox="version_control",
    ),
    "git_diff": ToolDefinition(
        schema=_git_diff_schema,
        handler=_git_diff_handler,
        permission="allowlist",
        help_text="git diff",
        toolbox="version_control",
    ),
}

__all__ = ["git_readonly_tools"]
