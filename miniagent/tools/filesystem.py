"""Mini Agent Python — 文件系统工具

提供安全的文件操作工具，支持沙箱路径验证。

工具列表：
- read_file: 读取文件内容，支持分页和 UTF-8 编码
- write_file: 写入文件，自动创建父目录
- edit_file: 行级编辑，要求唯一匹配替换
- list_dir: 列出目录内容，支持递归、详情模式（size）、深度与条目上限
- create_dir: 创建目录，支持递归创建
- move_file: 移动/重命名文件或目录
- copy_file: 复制文件，保留元数据
- delete_file: 删除文件/目录，需要权限确认（require-confirm）

安全机制：
- 所有路径必须通过 resolve_path_for_tool 验证
- 路径必须在 allowed_paths 沙箱范围内
- 禁止访问系统关键目录（通过沙箱配置）
- 自动规范化路径防止路径逃逸攻击

使用示例：
    >>> ctx = ToolContext(cwd="/workspace", allowed_paths=["/workspace"])
    >>> result = await _read_file_handler({"path": "data.txt"}, ctx)
    >>> print(result.content)

性能优化：
- 使用 asyncio.to_thread 包装阻塞 I/O
- 分页读取避免大文件过载上下文
- 目录列表支持 ``max_depth`` / ``max_entries`` 防止递归爆炸

重构说明：
- 使用 ToolBuilder 链式调用简化工具定义
- 相比原始实现代码量减少约 67%
- 所有 handler 独立定义便于测试

设计背景见 docs/ARCHITECTURE.md § 工具层，安全边界见 docs/SECURITY.md。
"""

from __future__ import annotations

import asyncio
import hashlib
import heapq
import os
import shutil
from pathlib import Path
from typing import Any

from miniagent.core.constants import KNOWLEDGE_MAX_FILE_CHARS
from miniagent.infrastructure.atomic_json import atomic_write_text
from miniagent.infrastructure.json_config import get_config
from miniagent.knowledge.file_ingest import ingest_file_for_analysis
from miniagent.tools.base import tool
from miniagent.tools.path_utils import resolve_path_for_tool
from miniagent.types.error_prefix import ERROR_PREFIX, SUCCESS_PREFIX
from miniagent.types.tool import ToolContext, ToolDefinition, ToolResult

# ════════════════════════════════════════════════════════
# Handlers
# ════════════════════════════════════════════════════════


def _read_file_page_sync(
    file_path: str,
    offset: int,
    limit: int,
    ingest_cap: int,
) -> tuple[list[str], int, str, str, bool, int]:
    """Read one page while hashing/counting the full decoded file in one pass."""
    page: list[str] = []
    ingest_parts: list[str] = []
    ingest_length = 0
    digest = hashlib.sha256()
    contains_nul = False
    total = 0
    ended_with_newline = False
    with open(file_path, encoding="utf-8", newline=None) as handle:
        for raw_line in handle:
            total += 1
            digest.update(raw_line.encode("utf-8", errors="replace"))
            contains_nul = contains_nul or "\x00" in raw_line
            if ingest_length < ingest_cap:
                piece = raw_line[: ingest_cap - ingest_length]
                ingest_parts.append(piece)
                ingest_length += len(piece)
            if offset <= total < offset + limit:
                page.append(raw_line[:-1] if raw_line.endswith("\n") else raw_line)
            ended_with_newline = raw_line.endswith("\n")

    # str.split("\n") yields one empty row for an empty file and a trailing
    # empty row when the file ends with a newline. Preserve that contract.
    if total == 0:
        total = 1
        if offset <= 1 < offset + limit:
            page.append("")
    elif ended_with_newline:
        total += 1
        if offset <= total < offset + limit:
            page.append("")

    return (
        page,
        total,
        "".join(ingest_parts),
        digest.hexdigest(),
        contains_nul,
        os.path.getsize(file_path),
    )


def _limited_sorted_items(directory: Path, limit: int) -> tuple[list[Path], bool]:
    """Return the first sorted entries with O(limit) auxiliary memory."""
    items = heapq.nsmallest(
        limit + 1,
        directory.iterdir(),
        key=lambda item: (not item.is_dir(), item.name),
    )
    return items[:limit], len(items) > limit


async def _read_file_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """读取文件内容。

    支持分页读取（offset/limit 参数），避免大文件一次性加载到上下文。
    返回总行数和已读取行数作为 meta 信息，方便 LLM 判断是否需要继续读取。
    """
    file_path, path_err = resolve_path_for_tool(str(args["path"]), ctx)
    if path_err:
        return path_err
    try:
        offset = int(args.get("offset", 1))
        limit = int(args.get("limit", 1000))
    except (TypeError, ValueError):
        return ToolResult(success=False, content=f"{ERROR_PREFIX} offset/limit 必须是整数")
    offset = max(1, min(offset, 10_000_000))
    limit = max(1, min(limit, 10_000))
    ingest_cap = int(
        get_config(
            "knowledge.auto_ingest_max_file_chars",
            get_config("knowledge.max_file_chars", KNOWLEDGE_MAX_FILE_CHARS),
        )
    )

    try:
        page, total, ingest_content, source_hash, contains_nul, input_bytes = (
            await asyncio.to_thread(
                _read_file_page_sync,
                file_path,
                offset,
                limit,
                max(1, ingest_cap + 1),
            )
        )
    except FileNotFoundError:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 文件不存在: {args['path']}")
    except PermissionError:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 权限不足，无法读取: {args['path']}")
    except IsADirectoryError:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 路径是目录而非文件: {args['path']}")
    except UnicodeDecodeError:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 文件不是有效 UTF-8 文本: {args['path']}")

    result = "\n".join(page)

    if len(page) < total:
        result += f"\n... (共 {total} 行，仅显示 {len(page)} 行，使用 offset/limit 翻页)"

    meta: dict[str, Any] = {
        "totalLines": total,
        "readLines": len(page),
        "input_bytes": input_bytes,
        "output_chars": len(result),
    }
    ingest = await asyncio.to_thread(
        ingest_file_for_analysis,
        file_path,
        content=ingest_content,
        source_hash=source_hash,
        contains_nul=contains_nul,
        registry=ctx.knowledge_registry,
    )
    meta.update(
        {
            "rag_ingested": bool(ingest.success and not ingest.skipped),
            "rag_ingest_skipped": bool(ingest.skipped),
            "rag_ingest_reason": ingest.reason,
            "rag_kb_name": ingest.kb_name,
            "source_path": ingest.source_path,
            "source_hash": ingest.source_hash,
        }
    )

    return ToolResult(success=True, content=result, meta=meta)


async def _write_file_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """写入或创建文件。自动创建中间目录。"""
    file_path, path_err = resolve_path_for_tool(str(args["path"]), ctx)
    if path_err:
        return path_err
    content = str(args["content"])

    try:
        await asyncio.to_thread(atomic_write_text, file_path, content, encoding="utf-8")
    except PermissionError:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 权限不足，无法写入: {args['path']}")
    except OSError as e:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 写入失败: {e}")

    return ToolResult(success=True, content=f"{SUCCESS_PREFIX} 已写入 {file_path} ({len(content)} 字节)")


async def _edit_file_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """精确替换文件中的文本（只替换唯一匹配的一处）。"""
    file_path, path_err = resolve_path_for_tool(str(args["path"]), ctx)
    if path_err:
        return path_err
    old_text = str(args["oldText"])
    new_text = str(args["newText"])

    try:
        content = await asyncio.to_thread(Path(file_path).read_text, encoding="utf-8")
    except FileNotFoundError:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 文件不存在: {file_path}")
    except OSError as e:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 读取文件失败: {e}")

    occurrences = content.count(old_text)

    if occurrences == 0:
        return ToolResult(success=False, content=f'{ERROR_PREFIX} 未找到匹配的文本: "{old_text[:50]}..."')
    if occurrences > 1:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 找到 {occurrences} 处匹配，请提供更精确的 oldText")

    updated = content.replace(old_text, new_text, 1)
    await asyncio.to_thread(atomic_write_text, file_path, updated, encoding="utf-8")

    return ToolResult(success=True, content=f"{SUCCESS_PREFIX} 已替换 1 处 ({len(old_text)} → {len(new_text)} 字符)")


async def _list_dir_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """列出目录内容（文件和子目录）。递归时受 max_depth / max_entries 约束。"""
    dir_path, path_err = resolve_path_for_tool(str(args["path"]), ctx)
    if path_err:
        return path_err
    recursive = bool(args.get("recursive", False))
    detail = bool(args.get("detail", False))
    try:
        max_depth = int(args.get("max_depth", 8))
    except (TypeError, ValueError):
        max_depth = 8
    try:
        max_entries = int(args.get("max_entries", 200))
    except (TypeError, ValueError):
        max_entries = 200
    max_depth = max(1, min(max_depth, 32))
    max_entries = max(1, min(max_entries, 2000))

    p = Path(dir_path)
    exists, is_dir = await asyncio.to_thread(lambda: (p.exists(), p.is_dir()))
    if not exists or not is_dir:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 目录不存在: {dir_path}")

    def _format_entry(item: Path) -> str:
        icon = "📁 " if item.is_dir() else "📄 "
        label = f"{icon}{item.name}"
        if not detail:
            return label
        try:
            stat = item.stat()
            if item.is_dir():
                return f"{label} [dir, mtime={int(stat.st_mtime)}]"
            return f"{label} [{stat.st_size} B, mtime={int(stat.st_mtime)}]"
        except OSError:
            return label

    if recursive:
        entries: list[str] = []
        truncated = False

        def _walk(d: Path, prefix: str, depth: int) -> None:
            nonlocal truncated
            if len(entries) >= max_entries:
                truncated = True
                return
            if depth > max_depth:
                return
            try:
                remaining = max_entries - len(entries)
                items, has_more = _limited_sorted_items(d, remaining)
                truncated = truncated or has_more
            except PermissionError:
                return
            for item in items:
                if len(entries) >= max_entries:
                    truncated = True
                    return
                entries.append(f"{prefix}{_format_entry(item)}")
                if item.is_dir() and depth < max_depth:
                    _walk(item, prefix + "  ", depth + 1)

        await asyncio.to_thread(_walk, p, "", 1)
        content = "\n".join(entries)
        if truncated:
            content += f"\n... (已截断，最多 {max_entries} 条；可调 max_entries / max_depth)"
        return ToolResult(success=True, content=content)

    def _list_lines() -> tuple[list[str], bool]:
        items, truncated = _limited_sorted_items(p, max_entries)
        return [_format_entry(entry) for entry in items], truncated

    lines, truncated = await asyncio.to_thread(_list_lines)
    content = "\n".join(lines)
    if truncated:
        content += f"\n... (已截断，最多 {max_entries} 条；可调 max_entries)"
    return ToolResult(success=True, content=content)


async def _create_dir_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """创建新目录。默认递归创建父目录。"""
    dir_path, path_err = resolve_path_for_tool(str(args["path"]), ctx)
    if path_err:
        return path_err
    recursive = args.get("recursive", True)
    await asyncio.to_thread(os.makedirs, dir_path, exist_ok=bool(recursive))
    return ToolResult(success=True, content=f"{SUCCESS_PREFIX} 已创建目录: {dir_path}")


async def _move_file_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """移动文件或重命名。自动创建目标父目录。"""
    src, src_err = resolve_path_for_tool(str(args["from"]), ctx)
    if src_err:
        return src_err
    dst, dst_err = resolve_path_for_tool(str(args["to"]), ctx)
    if dst_err:
        return dst_err

    if not await asyncio.to_thread(os.path.exists, src):
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 源文件不存在: {src}")

    try:
        def _move() -> None:
            parent = os.path.dirname(dst)
            if parent:
                os.makedirs(parent, exist_ok=True)
            shutil.move(src, dst)

        await asyncio.to_thread(_move)
        return ToolResult(success=True, content=f"{SUCCESS_PREFIX} 已移动: {src} → {dst}")
    except OSError as e:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 移动失败: {e}")


async def _copy_file_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """复制文件。保留元数据。"""
    src, src_err = resolve_path_for_tool(str(args["from"]), ctx)
    if src_err:
        return src_err
    dst, dst_err = resolve_path_for_tool(str(args["to"]), ctx)
    if dst_err:
        return dst_err

    if not await asyncio.to_thread(os.path.exists, src):
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 源文件不存在: {src}")

    try:
        def _copy() -> None:
            parent = os.path.dirname(dst)
            if parent:
                os.makedirs(parent, exist_ok=True)
            shutil.copy2(src, dst)

        await asyncio.to_thread(_copy)
        return ToolResult(success=True, content=f"{SUCCESS_PREFIX} 已复制: {src} → {dst}")
    except OSError as e:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 复制失败: {e}")


async def _delete_file_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """删除文件或目录（危险操作）。"""
    file_path, path_err = resolve_path_for_tool(str(args["path"]), ctx)
    if path_err:
        return path_err
    recursive = bool(args.get("recursive", False))

    p = Path(file_path)
    is_dir = await asyncio.to_thread(p.is_dir)
    if is_dir and not recursive:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 删除目录需设置 recursive=true")

    def _delete() -> None:
        if is_dir:
            shutil.rmtree(file_path)
        else:
            p.unlink()

    await asyncio.to_thread(_delete)

    return ToolResult(success=True, content=f"{SUCCESS_PREFIX} 已删除: {file_path}")


# ════════════════════════════════════════════════════════
# Tool Definitions (使用 ToolBuilder)
# ════════════════════════════════════════════════════════

filesystem_tools: dict[str, ToolDefinition] = {
    "read_file": tool("read_file", "读取文件内容")
        .param("path", "string", "文件路径")
        .optional("offset", "number", "起始行号（1-indexed）")
        .optional("limit", "number", "最大读取行数")
        .sandbox()
        .toolbox("file_read")
        .handler(_read_file_handler)
        .build(),
    "write_file": tool("write_file", "写入文件（创建新文件或覆盖已有文件）")
        .param("path", "string", "文件路径")
        .param("content", "string", "要写入的内容")
        .sandbox()
        .toolbox("file_write")
        .handler(_write_file_handler)
        .build(),
    "edit_file": tool("edit_file", "精确替换文件中的文本（只替换唯一匹配的一处）")
        .param("path", "string", "文件路径")
        .param("oldText", "string", "要替换的原文（必须唯一匹配）")
        .param("newText", "string", "替换为的新文本")
        .sandbox()
        .toolbox("file_write")
        .handler(_edit_file_handler)
        .build(),
    "list_dir": tool("list_dir", "列出目录内容（文件和子目录）")
        .param("path", "string", "目录路径")
        .optional("recursive", "boolean", "是否递归列出子目录")
        .optional("detail", "boolean", "是否显示文件大小/目录 mtime 等详情")
        .optional("max_depth", "number", "递归最大深度（默认 8，仅 recursive=true 时生效）")
        .optional("max_entries", "number", "最大返回条目数（默认 200）")
        .sandbox()
        .toolbox("dir_ops")
        .handler(_list_dir_handler)
        .build(),
    "create_dir": tool("create_dir", "创建新目录")
        .param("path", "string", "目录路径")
        .optional("recursive", "boolean", "是否递归创建父目录（默认 true）")
        .sandbox()
        .toolbox("dir_ops")
        .handler(_create_dir_handler)
        .build(),
    "move_file": tool("move_file", "移动文件或重命名")
        .param("from", "string", "源文件路径")
        .param("to", "string", "目标文件路径")
        .sandbox()
        .toolbox("dir_ops")
        .handler(_move_file_handler)
        .build(),
    "copy_file": tool("copy_file", "复制文件")
        .param("from", "string", "源文件路径")
        .param("to", "string", "目标文件路径")
        .sandbox()
        .toolbox("dir_ops")
        .handler(_copy_file_handler)
        .build(),
    "delete_file": tool("delete_file", "删除文件或目录（危险操作！）")
        .param("path", "string", "要删除的路径")
        .optional("recursive", "boolean", "是否递归删除目录内容")
        .require_confirm()
        .toolbox("dir_ops")
        .handler(_delete_file_handler)
        .build(),
}

__all__ = ["filesystem_tools"]
