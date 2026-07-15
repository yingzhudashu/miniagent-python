"""Mini Agent Python — 数据处理工具

- ``read_csv``: 读取 CSV/TSV 文件，支持分隔符/编码/行数限制
- ``write_csv``: 将列表数据写入 CSV 文件
- ``json_read``: 读取 JSON/JSONL 文件
- ``json_write``: 写入 JSON 文件（支持 pretty/compact 格式）

重构说明：使用 ToolBuilder 简化工具定义。
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import os
from typing import Any

from miniagent.agent.types.error_prefix import ERROR_PREFIX, SUCCESS_PREFIX
from miniagent.agent.types.tool import ToolContext, ToolDefinition, ToolResult
from miniagent.assistant.infrastructure.atomic_json import atomic_dump_json, atomic_write_text
from miniagent.assistant.tools.base import tool
from miniagent.assistant.tools.path_utils import resolve_path_for_tool

# ════════════════════════════════════════════════════════
# Handlers
# ════════════════════════════════════════════════════════


def _read_csv_sync(
    path: str,
    delimiter: str,
    encoding: str,
    max_rows: int,
) -> ToolResult:
    try:
        with open(path, encoding=encoding, newline="") as file:
            if not delimiter:
                sample = file.read(65536)
                delimiter = "\t" if sample.count("\t") > sample.count(",") else ","
                file.seek(0)
            reader = csv.DictReader(file, delimiter=delimiter)
            rows = []
            for index, row in enumerate(reader):
                if index >= max_rows:
                    break
                rows.append(dict(row))
            if not rows:
                return ToolResult(success=True, content="(空文件)")
            header = list(rows[0].keys())
            buffer = io.StringIO()
            writer = csv.DictWriter(buffer, fieldnames=header)
            writer.writeheader()
            writer.writerows(rows)
            content = buffer.getvalue()
            if len(content) > 1_000_000:
                content = content[:1_000_000] + "\n... (已截断)"
            return ToolResult(success=True, content=content)
    except Exception as error:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 读取失败: {error}")


async def _read_csv_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """读取 CSV 文件内容。自动检测分隔符。"""
    path, path_err = resolve_path_for_tool(str(args["path"]), ctx)
    if path_err:
        return path_err
    assert path is not None
    if not await asyncio.to_thread(os.path.isfile, path):
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 文件不存在: {path}")

    delimiter = str(args.get("delimiter", "")).strip()
    encoding = str(args.get("encoding", "utf-8"))
    try:
        max_rows = int(args.get("maxRows", 100))
    except (TypeError, ValueError):
        return ToolResult(success=False, content=f"{ERROR_PREFIX} maxRows 必须是整数")
    max_rows = max(1, min(max_rows, 10_000))

    return await asyncio.to_thread(
        _read_csv_sync,
        path,
        delimiter,
        encoding,
        max_rows,
    )


def _write_csv_sync(path: str, raw_data: str, delimiter: str) -> ToolResult:
    try:
        data = json.loads(raw_data)
    except json.JSONDecodeError as error:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} data 不是有效 JSON: {error}")
    try:
        if not isinstance(data, list) or not data:
            return ToolResult(success=False, content=f"{ERROR_PREFIX} data 必须是非空数组")
        if len(data) > 10_000:
            return ToolResult(success=False, content=f"{ERROR_PREFIX} data 最多允许 10000 行")
        buffer = io.StringIO(newline="")
        if isinstance(data[0], dict) and all(isinstance(row, dict) for row in data):
            fieldnames = list(data[0].keys())
            dict_writer = csv.DictWriter(buffer, fieldnames=fieldnames, delimiter=delimiter)
            dict_writer.writeheader()
            dict_writer.writerows(data)
        elif isinstance(data[0], list) and all(isinstance(row, list) for row in data):
            row_writer = csv.writer(buffer, delimiter=delimiter)
            row_writer.writerows(data)
        else:
            return ToolResult(
                success=False,
                content=f"{ERROR_PREFIX} data 各行必须统一为对象或数组",
            )
        count = len(data)
        atomic_write_text(path, buffer.getvalue(), encoding="utf-8")
        return ToolResult(success=True, content=f"{SUCCESS_PREFIX} 已写入 {count} 行到 {path}")
    except Exception as error:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 写入失败: {error}")


async def _write_csv_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """将数据写入 CSV 文件。支持对象数组或二维数组。"""
    path, path_err = resolve_path_for_tool(str(args["path"]), ctx)
    if path_err:
        return path_err
    assert path is not None
    delimiter = str(args.get("delimiter", ",")).strip() or ","
    raw_data = str(args.get("data", ""))

    return await asyncio.to_thread(_write_csv_sync, path, raw_data, delimiter)


def _json_read_sync(path: str, encoding: str, max_chars: int) -> ToolResult:
    try:
        if path.endswith(".jsonl"):
            parts = ["["]
            output_length = 1
            emitted = 0
            truncated = False
            with open(path, encoding=encoding) as file:
                for line in file:
                    if not line.strip():
                        continue
                    parsed_line = json.loads(line)
                    rendered = json.dumps(parsed_line, ensure_ascii=False, indent=2)
                    rendered = "\n".join("  " + row for row in rendered.splitlines())
                    prefix = "\n" if emitted == 0 else ",\n"
                    piece = prefix + rendered
                    if output_length + len(piece) + 2 <= max_chars:
                        parts.append(piece)
                        output_length += len(piece)
                    else:
                        truncated = True
                    emitted += 1
            parts.append("\n]")
            formatted = "[]" if emitted == 0 else "".join(parts)
            if truncated:
                formatted += "\n... (已截断)"
        else:
            with open(path, encoding=encoding) as file:
                parsed = json.load(file)
            formatted = json.dumps(parsed, ensure_ascii=False, indent=2)
            if len(formatted) > max_chars:
                formatted = formatted[:max_chars] + "\n... (已截断)"
        return ToolResult(success=True, content=formatted)
    except json.JSONDecodeError as error:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} JSON 解析失败: {error}")
    except Exception as error:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 读取失败: {error}")


async def _json_read_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """读取 JSON 或 JSONL 文件内容。自动格式化。"""
    path, path_err = resolve_path_for_tool(str(args["path"]), ctx)
    if path_err:
        return path_err
    assert path is not None
    if not await asyncio.to_thread(os.path.isfile, path):
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 文件不存在: {path}")

    encoding = str(args.get("encoding", "utf-8"))
    try:
        max_chars = int(args.get("maxChars", 50000))
    except (TypeError, ValueError):
        return ToolResult(success=False, content=f"{ERROR_PREFIX} maxChars 必须是整数")
    max_chars = max(100, min(max_chars, 1_000_000))

    return await asyncio.to_thread(_json_read_sync, path, encoding, max_chars)


def _json_write_sync(path: str, raw_data: str, pretty: bool) -> ToolResult:
    try:
        parsed = json.loads(raw_data)
    except json.JSONDecodeError as error:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} data 不是有效 JSON: {error}")
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        indent = 2 if pretty else None
        atomic_dump_json(path, parsed, ensure_ascii=False, indent=indent)
        size = os.path.getsize(path)
        return ToolResult(
            success=True,
            content=f"{SUCCESS_PREFIX} 已写入 JSON 到 {path}（{size} 字节）",
        )
    except Exception as error:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 写入失败: {error}")


async def _json_write_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """将数据写入 JSON 文件。支持美化输出。"""
    path, path_err = resolve_path_for_tool(str(args["path"]), ctx)
    if path_err:
        return path_err
    assert path is not None
    pretty = args.get("pretty", True) in (True, "true", "1")
    raw_data = str(args.get("data", ""))
    return await asyncio.to_thread(_json_write_sync, path, raw_data, pretty)


# ════════════════════════════════════════════════════════
# Tool Definitions (使用 ToolBuilder)
# ════════════════════════════════════════════════════════

data_tools: dict[str, ToolDefinition] = {
    "read_csv": tool("read_csv", "读取 CSV/TSV 文件，返回前 N 行数据")
        .param("path", "string", "CSV 文件路径")
        .optional("delimiter", "string", "分隔符（默认自动检测 , 或 \\t）")
        .optional("encoding", "string", "文件编码（默认 utf-8）")
        .optional("maxRows", "number", "最大返回行数（默认 100）")
        .sandbox()
        .toolbox("file_read")
        .handler(_read_csv_handler)
        .build(),
    "write_csv": tool("write_csv", "将数据写入 CSV 文件")
        .param("path", "string", "输出文件路径")
        .param("data", "string", "JSON 格式的二维数组或对象数组")
        .optional("delimiter", "string", "分隔符（默认 ,）")
        .sandbox()
        .toolbox("file_write")
        .handler(_write_csv_handler)
        .build(),
    "json_read": tool("json_read", "读取 JSON 或 JSONL 文件内容")
        .param("path", "string", "JSON/JSONL 文件路径")
        .optional("encoding", "string", "文件编码（默认 utf-8）")
        .optional("maxChars", "number", "最大返回字符数（默认 50000）")
        .sandbox()
        .toolbox("file_read")
        .handler(_json_read_handler)
        .build(),
    "json_write": tool("json_write", "将数据写入 JSON 文件")
        .param("path", "string", "输出文件路径")
        .param("data", "string", "要写入的 JSON 字符串")
        .optional("pretty", "boolean", "是否美化输出（缩进 2 空格，默认 true）")
        .sandbox()
        .toolbox("file_write")
        .handler(_json_write_handler)
        .build(),
}

__all__ = ["data_tools"]
