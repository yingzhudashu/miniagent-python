"""Mini Agent Python — 数据处理工具

- ``read_csv``: 读取 CSV/TSV 文件，支持分隔符/编码/行数限制
- ``write_csv``: 将列表数据写入 CSV 文件
- ``json_read``: 读取 JSON/JSONL 文件
- ``json_write``: 写入 JSON 文件（支持 pretty/compact 格式）

重构说明：使用 ToolBuilder 简化工具定义。
"""

from __future__ import annotations

import csv
import io
import json
import os
from typing import Any

from miniagent.tools.base import tool
from miniagent.tools.path_utils import resolve_path_from_ctx
from miniagent.types.error_prefix import ERROR_PREFIX, SUCCESS_PREFIX
from miniagent.types.tool import ToolContext, ToolDefinition, ToolResult

# ════════════════════════════════════════════════════════
# Handlers
# ════════════════════════════════════════════════════════


async def _read_csv_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """读取 CSV 文件内容。自动检测分隔符。"""
    path = resolve_path_from_ctx(str(args["path"]), ctx)
    if not os.path.isfile(path):
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 文件不存在: {path}")

    delimiter = str(args.get("delimiter", "")).strip()
    encoding = str(args.get("encoding", "utf-8"))
    max_rows = int(args.get("maxRows", 100))

    try:
        with open(path, encoding=encoding, newline="") as f:
            raw = f.read(8192)
            if not delimiter:
                delimiter = "\t" if raw.count("\t") > raw.count(",") else ","
            f.seek(0)
            reader = csv.DictReader(f, delimiter=delimiter)
            rows = []
            for i, row in enumerate(reader):
                if i >= max_rows:
                    break
                rows.append(dict(row))
            if rows:
                header = list(rows[0].keys())
                buf = io.StringIO()
                w = csv.DictWriter(buf, fieldnames=header)
                w.writeheader()
                w.writerows(rows)
                content = buf.getvalue()
            else:
                content = "(空文件)"
            return ToolResult(success=True, content=content)
    except Exception as e:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 读取失败: {e}")


async def _write_csv_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """将数据写入 CSV 文件。支持对象数组或二维数组。"""
    path = resolve_path_from_ctx(str(args["path"]), ctx)
    delimiter = str(args.get("delimiter", ",")).strip() or ","
    raw_data = str(args.get("data", ""))

    try:
        data = json.loads(raw_data)
    except json.JSONDecodeError as e:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} data 不是有效 JSON: {e}")

    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as f:
            if data and isinstance(data[0], dict):
                fieldnames = list(data[0].keys())
                writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=delimiter)
                writer.writeheader()
                writer.writerows(data)
                n = len(data)
            elif data and isinstance(data[0], list):
                writer = csv.writer(f, delimiter=delimiter)
                writer.writerows(data)
                n = len(data)
            else:
                return ToolResult(success=False, content=f"{ERROR_PREFIX} data 必须是非空数组")
        return ToolResult(success=True, content=f"{SUCCESS_PREFIX} 已写入 {n} 行到 {path}")
    except Exception as e:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 写入失败: {e}")


async def _json_read_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """读取 JSON 或 JSONL 文件内容。自动格式化。"""
    path = resolve_path_from_ctx(str(args["path"]), ctx)
    if not os.path.isfile(path):
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 文件不存在: {path}")

    encoding = str(args.get("encoding", "utf-8"))
    max_chars = int(args.get("maxChars", 50000))

    try:
        with open(path, encoding=encoding) as f:
            content = f.read()
        if path.endswith(".jsonl"):
            lines = content.strip().split("\n")
            parsed = [json.loads(line) for line in lines if line.strip()]
        else:
            parsed = json.loads(content)
        formatted = json.dumps(parsed, ensure_ascii=False, indent=2)
        if len(formatted) > max_chars:
            formatted = formatted[:max_chars] + "\n... (已截断)"
        return ToolResult(success=True, content=formatted)
    except json.JSONDecodeError as e:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} JSON 解析失败: {e}")
    except Exception as e:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 读取失败: {e}")


async def _json_write_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """将数据写入 JSON 文件。支持美化输出。"""
    path = resolve_path_from_ctx(str(args["path"]), ctx)
    pretty = args.get("pretty", True) in (True, "true", "1")
    raw_data = str(args.get("data", ""))

    try:
        parsed = json.loads(raw_data)
    except json.JSONDecodeError as e:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} data 不是有效 JSON: {e}")

    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        indent = 2 if pretty else None
        with open(path, "w", encoding="utf-8") as f:
            json.dump(parsed, f, ensure_ascii=False, indent=indent)
        size = os.path.getsize(path)
        return ToolResult(success=True, content=f"{SUCCESS_PREFIX} 已写入 JSON 到 {path}（{size} 字节）")
    except Exception as e:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 写入失败: {e}")


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