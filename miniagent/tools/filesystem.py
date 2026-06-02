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

所有操作受路径沙箱保护（:func:`miniagent.security.sandbox.resolve_sandbox_path`）。

越权路径拒绝行为见 ``docs/SECURITY.md``。
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from typing import Any

from miniagent.tools._path_utils import resolve_path_from_ctx
from miniagent.types.error_prefix import ERROR_PREFIX, SUCCESS_PREFIX
from miniagent.types.tool import ToolContext, ToolDefinition, ToolResult


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
    """读取文件内容。

    支持分页读取（offset/limit 参数），避免大文件一次性加载到上下文。
    返回总行数和已读取行数作为 meta 信息，方便 LLM 判断是否需要继续读取。

    Args:
        args: 包含 path（必需）、offset（可选，默认1）、limit（可选，默认1000）
        ctx: 工具执行上下文，提供沙箱路径限制

    Returns:
        ToolResult: 成功时包含文件内容（可能截断）和 meta 信息
    """
    file_path = resolve_path_from_ctx(str(args["path"]), ctx)
    offset = int(args.get("offset", 1))
    limit = int(args.get("limit", 1000))

    try:
        # 使用 asyncio.to_thread 避免阻塞事件循环
        content = await asyncio.to_thread(
            Path(file_path).read_text, encoding="utf-8"
        )
    except FileNotFoundError:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 文件不存在: {args['path']}")
    except PermissionError:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 权限不足，无法读取: {args['path']}")
    except IsADirectoryError:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 路径是目录而非文件: {args['path']}")
    lines = content.split("\n")
    total = len(lines)

    sliced = lines[offset - 1 : offset - 1 + limit]
    result = "\n".join(sliced)

    if len(sliced) < total:
        result += f"\n... (共 {total} 行，仅显示 {len(sliced)} 行，使用 offset/limit 翻页)"

    return ToolResult(
        success=True, content=result, meta={"totalLines": total, "readLines": len(sliced)}
    )


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
    """写入或创建文件。

    自动创建中间目录（os.makedirs exist_ok=True），适合 LLM 生成新文件。

    Args:
        args: 包含 path（文件路径）和 content（写入内容）
        ctx: 工具执行上下文

    Returns:
        ToolResult: 成功时返回写入的字节数
    """
    file_path = resolve_path_from_ctx(str(args["path"]), ctx)
    content = str(args["content"])

    parent = os.path.dirname(file_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    try:
        # 使用 asyncio.to_thread 避免阻塞事件循环
        await asyncio.to_thread(
            Path(file_path).write_text, content, encoding="utf-8"
        )
    except PermissionError:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 权限不足，无法写入: {args['path']}")
    except OSError as e:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 写入失败: {e}")

    return ToolResult(success=True, content=f"{SUCCESS_PREFIX} 已写入 {file_path} ({len(content)} 字节)")


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
    """精确替换文件中的文本（只替换唯一匹配的一处）。

    要求 oldText 在文件中只出现一次，避免意外替换多处内容。
    这是安全设计：LLM 经常给出不够精确的替换文本，此检查防止误改。

    Args:
        args: 包含 path（文件路径）、oldText（要替换的原文）、newText（新文本）
        ctx: 工具执行上下文

    Returns:
        ToolResult: 成功时返回替换信息；失败时提示未找到或多处匹配
    """
    file_path = resolve_path_from_ctx(str(args["path"]), ctx)
    old_text = str(args["oldText"])
    new_text = str(args["newText"])

    try:
        # 使用 asyncio.to_thread 避免阻塞事件循环
        content = await asyncio.to_thread(
            Path(file_path).read_text, encoding="utf-8"
        )
    except FileNotFoundError:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 文件不存在: {file_path}")
    except OSError as e:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 读取文件失败: {e}")

    occurrences = content.count(old_text)

    if occurrences == 0:
        return ToolResult(success=False, content=f'{ERROR_PREFIX} 未找到匹配的文本: "{old_text[:50]}..."')
    if occurrences > 1:
        return ToolResult(
            success=False, content=f"{ERROR_PREFIX} 找到 {occurrences} 处匹配，请提供更精确的 oldText"
        )

    updated = content.replace(old_text, new_text, 1)
    await asyncio.to_thread(
        Path(file_path).write_text, updated, encoding="utf-8"
    )

    return ToolResult(
        success=True, content=f"{SUCCESS_PREFIX} 已替换 1 处 ({len(old_text)} → {len(new_text)} 字符)"
    )


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
    """列出目录内容（文件和子目录）。

    递归模式使用树形缩进展示，目录优先排列（排序 key: not x.is_dir()）。
    递归结果上限 200 条，防止超大目录撑爆上下文。

    Args:
        args: 包含 path（目录路径）、recursive（可选，是否递归）
        ctx: 工具执行上下文

    Returns:
        ToolResult: 成功时返回带 emoji 图标的目录树
    """
    dir_path = resolve_path_from_ctx(str(args["path"]), ctx)
    recursive = bool(args.get("recursive", False))

    p = Path(dir_path)
    if not p.exists() or not p.is_dir():
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 目录不存在: {dir_path}")

    if recursive:
        entries: list[str] = []

        def _walk(d: Path, prefix: str) -> None:
            """递归遍历目录，构建树形列表。

            Args:
                d: 当前目录路径
                prefix: 缩进前缀
            """
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
    """创建新目录。

    默认递归创建父目录（exist_ok=True），幂等操作，已存在不报错。

    Args:
        args: 包含 path（目录路径）、recursive（可选，默认 True）
        ctx: 工具执行上下文

    Returns:
        ToolResult: 成功时返回创建的路径
    """
    dir_path = resolve_path_from_ctx(str(args["path"]), ctx)
    recursive = args.get("recursive", True)
    os.makedirs(dir_path, exist_ok=bool(recursive))
    return ToolResult(success=True, content=f"{SUCCESS_PREFIX} 已创建目录: {dir_path}")


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
    """移动文件或重命名。

    自动创建目标文件的父目录，使用 shutil.move 支持跨文件系统移动。

    Args:
        args: 包含 from（源路径）和 to（目标路径）
        ctx: 工具执行上下文

    Returns:
        ToolResult: 成功时返回移动前后的路径
    """
    src = resolve_path_from_ctx(str(args["from"]), ctx)
    dst = resolve_path_from_ctx(str(args["to"]), ctx)

    if not os.path.exists(src):
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 源文件不存在: {src}")

    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(src, dst)
        return ToolResult(success=True, content=f"{SUCCESS_PREFIX} 已移动: {src} → {dst}")
    except OSError as e:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 移动失败: {e}")


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
    """复制文件。

    使用 shutil.copy2 保留元数据（修改时间、权限等）。

    Args:
        args: 包含 from（源路径）和 to（目标路径）
        ctx: 工具执行上下文

    Returns:
        ToolResult: 成功时返回复制前后的路径
    """
    src = resolve_path_from_ctx(str(args["from"]), ctx)
    dst = resolve_path_from_ctx(str(args["to"]), ctx)

    if not os.path.exists(src):
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 源文件不存在: {src}")

    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        return ToolResult(success=True, content=f"{SUCCESS_PREFIX} 已复制: {src} → {dst}")
    except OSError as e:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 复制失败: {e}")


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
    """删除文件或目录（危险操作）。

    此工具标记为 require-confirm 权限，执行前需用户确认。
    删除目录必须设置 recursive=true，防止意外删除整个目录。

    Args:
        args: 包含 path（要删除的路径）、recursive（可选，删除目录时需设为 True）
        ctx: 工具执行上下文

    Returns:
        ToolResult: 成功时返回删除的路径；目录非递归时返回错误
    """
    file_path = resolve_path_from_ctx(str(args["path"]), ctx)
    recursive = bool(args.get("recursive", False))

    p = Path(file_path)
    if p.is_dir():
        if not recursive:
            return ToolResult(success=False, content=f"{ERROR_PREFIX} 删除目录需设置 recursive=true")
        shutil.rmtree(file_path)
    else:
        p.unlink()

    return ToolResult(success=True, content=f"{SUCCESS_PREFIX} 已删除: {file_path}")


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
