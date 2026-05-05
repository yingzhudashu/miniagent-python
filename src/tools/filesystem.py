"""Mini Agent Python — 文件系统工具 (Phase 5)

提供 8 个文件操作工具：
- read_file: 读取文件（支持分页）
- write_file: 写入/创建文件
- edit_file: 精确替换文本（要求唯一匹配）
- list_dir: 列出目录内容（支持递归）
- create_dir: 创建目录
- move_file: 移动/重命名
- copy_file: 复制文件
- delete_file: 删除文件/目录（require-confirm）

所有操作受路径沙箱保护。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from src.types.tool import ToolDefinition, ToolContext, ToolResult
from src.security.sandbox import resolve_sandbox_path, get_default_workspace


def _allowed_dirs(ctx: ToolContext) -> list[str]:
    """获取允许的目录列表。"""
    return ctx.allowed_paths if ctx.allowed_paths else [get_default_workspace()]


# ════════════════════════════════════════════════════════
# read_file
# ════════════════════════════════════════════════════════

_read_file_schema = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "读取文件内容",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "offset": {"type": "number", "description": "起始行号（1-indexed）"},
                "limit": {"type": "number", "description": "最大读取行数"},
            },
            "required": ["path"],
        },
    },
}


async def _read_file_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    file_path = resolve_sandbox_path(str(args["path"]), _allowed_dirs(ctx))
    offset = int(args.get("offset", 1))
    limit = int(args.get("limit", 1000))

    content = Path(file_path).read_text(encoding="utf-8")
    lines = content.split("\n")
    total = len(lines)

    sliced = lines[offset - 1 : offset - 1 + limit]
    result = "\n".join(sliced)

    if len(sliced) < total:
        result += f"\n... (共 {total} 行，仅显示 {len(sliced)} 行，使用 offset/limit 翻页)"

    return ToolResult(success=True, content=result, meta={"totalLines": total, "readLines": len(sliced)})


# ════════════════════════════════════════════════════════
# write_file
# ════════════════════════════════════════════════════════

_write_file_schema = {
    "type": "function",
    "function": {
        "name": "write_file",
        "description": "写入文件（创建新文件或覆盖已有文件）",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "content": {"type": "string", "description": "要写入的内容"},
            },
            "required": ["path", "content"],
        },
    },
}


async def _write_file_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    file_path = resolve_sandbox_path(str(args["path"]), _allowed_dirs(ctx))
    content = str(args["content"])

    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    Path(file_path).write_text(content, encoding="utf-8")

    return ToolResult(success=True, content=f"✅ 已写入 {file_path} ({len(content)} 字节)")


# ════════════════════════════════════════════════════════
# edit_file
# ════════════════════════════════════════════════════════

_edit_file_schema = {
    "type": "function",
    "function": {
        "name": "edit_file",
        "description": "精确替换文件中的文本（只替换唯一匹配的一处）",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "oldText": {"type": "string", "description": "要替换的原文（必须唯一匹配）"},
                "newText": {"type": "string", "description": "替换为的新文本"},
            },
            "required": ["path", "oldText", "newText"],
        },
    },
}


async def _edit_file_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    file_path = resolve_sandbox_path(str(args["path"]), _allowed_dirs(ctx))
    old_text = str(args["oldText"])
    new_text = str(args["newText"])

    content = Path(file_path).read_text(encoding="utf-8")
    occurrences = content.count(old_text)

    if occurrences == 0:
        return ToolResult(success=False, content=f"❌ 未找到匹配的文本: \"{old_text[:50]}...\"")
    if occurrences > 1:
        return ToolResult(success=False, content=f"❌ 找到 {occurrences} 处匹配，请提供更精确的 oldText")

    updated = content.replace(old_text, new_text, 1)
    Path(file_path).write_text(updated, encoding="utf-8")

    return ToolResult(success=True, content=f"✅ 已替换 1 处 ({len(old_text)} → {len(new_text)} 字符)")


# ════════════════════════════════════════════════════════
# list_dir
# ════════════════════════════════════════════════════════

_list_dir_schema = {
    "type": "function",
    "function": {
        "name": "list_dir",
        "description": "列出目录内容（文件和子目录）",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "目录路径"},
                "recursive": {"type": "boolean", "description": "是否递归列出子目录"},
            },
            "required": ["path"],
        },
    },
}


async def _list_dir_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    dir_path = resolve_sandbox_path(str(args["path"]), _allowed_dirs(ctx))
    recursive = bool(args.get("recursive", False))

    p = Path(dir_path)
    if not p.exists() or not p.is_dir():
        return ToolResult(success=False, content=f"❌ 目录不存在: {dir_path}")

    if recursive:
        entries: list[str] = []

        def _walk(d: Path, prefix: str) -> None:
            try:
                items = sorted(d.iterdir(), key=lambda x: (not x.is_dir(), x.name))
            except PermissionError:
                return
            for item in items:
                icon = "📁 " if item.is_dir() else "📄 "
                entries.append(f"{prefix}{icon}{item.name}")
                if item.is_dir():
                    _walk(item, prefix + "  ")

        _walk(p, "")
        return ToolResult(success=True, content="\n".join(entries[:200]))

    items = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name))
    lines = [f"{'📁 ' if e.is_dir() else '📄 '}{e.name}" for e in items]
    return ToolResult(success=True, content="\n".join(lines))


# ════════════════════════════════════════════════════════
# create_dir
# ════════════════════════════════════════════════════════

_create_dir_schema = {
    "type": "function",
    "function": {
        "name": "create_dir",
        "description": "创建新目录",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "目录路径"},
                "recursive": {"type": "boolean", "description": "是否递归创建父目录（默认 true）"},
            },
            "required": ["path"],
        },
    },
}


async def _create_dir_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    dir_path = resolve_sandbox_path(str(args["path"]), _allowed_dirs(ctx))
    recursive = args.get("recursive", True)
    os.makedirs(dir_path, exist_ok=bool(recursive))
    return ToolResult(success=True, content=f"✅ 已创建目录: {dir_path}")


# ════════════════════════════════════════════════════════
# move_file
# ════════════════════════════════════════════════════════

_move_file_schema = {
    "type": "function",
    "function": {
        "name": "move_file",
        "description": "移动文件或重命名",
        "parameters": {
            "type": "object",
            "properties": {
                "from": {"type": "string", "description": "源文件路径"},
                "to": {"type": "string", "description": "目标文件路径"},
            },
            "required": ["from", "to"],
        },
    },
}


async def _move_file_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    src = resolve_sandbox_path(str(args["from"]), _allowed_dirs(ctx))
    dst = resolve_sandbox_path(str(args["to"]), _allowed_dirs(ctx))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.move(src, dst)
    return ToolResult(success=True, content=f"✅ 已移动: {src} → {dst}")


# ════════════════════════════════════════════════════════
# copy_file
# ════════════════════════════════════════════════════════

_copy_file_schema = {
    "type": "function",
    "function": {
        "name": "copy_file",
        "description": "复制文件",
        "parameters": {
            "type": "object",
            "properties": {
                "from": {"type": "string", "description": "源文件路径"},
                "to": {"type": "string", "description": "目标文件路径"},
            },
            "required": ["from", "to"],
        },
    },
}


async def _copy_file_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    src = resolve_sandbox_path(str(args["from"]), _allowed_dirs(ctx))
    dst = resolve_sandbox_path(str(args["to"]), _allowed_dirs(ctx))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)
    return ToolResult(success=True, content=f"✅ 已复制: {src} → {dst}")


# ════════════════════════════════════════════════════════
# delete_file
# ════════════════════════════════════════════════════════

_delete_file_schema = {
    "type": "function",
    "function": {
        "name": "delete_file",
        "description": "删除文件或目录（危险操作！）",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "要删除的路径"},
                "recursive": {"type": "boolean", "description": "是否递归删除目录内容"},
            },
            "required": ["path"],
        },
    },
}


async def _delete_file_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    file_path = resolve_sandbox_path(str(args["path"]), _allowed_dirs(ctx))
    recursive = bool(args.get("recursive", False))

    p = Path(file_path)
    if p.is_dir():
        if not recursive:
            return ToolResult(success=False, content="❌ 删除目录需设置 recursive=true")
        shutil.rmtree(file_path)
    else:
        p.unlink()

    return ToolResult(success=True, content=f"✅ 已删除: {file_path}")


# ════════════════════════════════════════════════════════
# 导出
# ════════════════════════════════════════════════════════

filesystem_tools: dict[str, ToolDefinition] = {
    "read_file": ToolDefinition(
        schema=_read_file_schema,
        handler=_read_file_handler,
        permission="sandbox",
        help_text="读取文件内容",
        toolbox="file_read",
    ),
    "write_file": ToolDefinition(
        schema=_write_file_schema,
        handler=_write_file_handler,
        permission="sandbox",
        help_text="写入/创建文件",
        toolbox="file_write",
    ),
    "edit_file": ToolDefinition(
        schema=_edit_file_schema,
        handler=_edit_file_handler,
        permission="sandbox",
        help_text="精确替换文件中的文本",
        toolbox="file_write",
    ),
    "list_dir": ToolDefinition(
        schema=_list_dir_schema,
        handler=_list_dir_handler,
        permission="sandbox",
        help_text="列出目录内容",
        toolbox="dir_ops",
    ),
    "create_dir": ToolDefinition(
        schema=_create_dir_schema,
        handler=_create_dir_handler,
        permission="sandbox",
        help_text="创建目录",
        toolbox="dir_ops",
    ),
    "move_file": ToolDefinition(
        schema=_move_file_schema,
        handler=_move_file_handler,
        permission="sandbox",
        help_text="移动/重命名文件",
        toolbox="dir_ops",
    ),
    "copy_file": ToolDefinition(
        schema=_copy_file_schema,
        handler=_copy_file_handler,
        permission="sandbox",
        help_text="复制文件",
        toolbox="dir_ops",
    ),
    "delete_file": ToolDefinition(
        schema=_delete_file_schema,
        handler=_delete_file_handler,
        permission="require-confirm",
        help_text="删除文件/目录（危险）",
        toolbox="dir_ops",
    ),
}

__all__ = ["filesystem_tools"]
