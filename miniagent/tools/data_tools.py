"""Mini Agent Python — 数据处理工具

- ``read_csv``: 读取 CSV/TSV 文件，支持分隔符/编码/行数限制
- ``write_csv``: 将列表数据写入 CSV 文件
- ``json_read``: 读取 JSON/JSONL 文件
- ``json_write``: 写入 JSON 文件（支持 pretty/compact 格式）
"""

from __future__ import annotations

import csv
import io
import json
import os
from typing import Any

from miniagent.security.sandbox import get_default_workspace, resolve_sandbox_path
from miniagent.types.tool import ToolContext, ToolDefinition, ToolResult


def _allowed_dirs(ctx: ToolContext) -> list[str]:
    """获取允许的目录列表，优先使用 ToolContext.allowed_paths。"""
    return ctx.allowed_paths if ctx.allowed_paths else [get_default_workspace()]


def _resolve_path(path: str, ctx: ToolContext) -> str:
    """将路径解析为沙箱允许范围内的绝对路径。"""
    return resolve_sandbox_path(path, _allowed_dirs(ctx))


# ─── read_csv ────────────────────────────────────────────

_read_csv_schema = {
    "type": "function",
    "function": {
        "name": "read_csv",
        "description": "读取 CSV/TSV 文件，返回前 N 行数据",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "CSV 文件路径"},
                "delimiter": {"type": "string", "description": "分隔符（默认自动检测 , 或 \\t）"},
                "encoding": {"type": "string", "description": "文件编码（默认 utf-8）"},
                "maxRows": {"type": "number", "description": "最大返回行数（默认 100）"},
            },
            "required": ["path"],
        },
    },
}


async def _read_csv_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    path = _resolve_path(str(args["path"]), ctx)
    if not os.path.isfile(path):
        return ToolResult(success=False, content=f"❌ 文件不存在: {path}")
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
        return ToolResult(success=False, content=f"❌ 读取失败: {e}")


# ─── write_csv ───────────────────────────────────────────

_write_csv_schema = {
    "type": "function",
    "function": {
        "name": "write_csv",
        "description": "将数据写入 CSV 文件",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "输出文件路径"},
                "data": {
                    "type": "string",
                    "description": "JSON 格式的二维数组或对象数组，如 [{\"name\":\"a\"},{\"name\":\"b\"}] 或 [[\"a\",1],[\"b\",2]]",
                },
                "delimiter": {"type": "string", "description": "分隔符（默认 ,）"},
            },
            "required": ["path", "data"],
        },
    },
}


async def _write_csv_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    path = _resolve_path(str(args["path"]), ctx)
    delimiter = str(args.get("delimiter", ",")).strip() or ","
    raw_data = str(args.get("data", ""))
    try:
        data = json.loads(raw_data)
    except json.JSONDecodeError as e:
        return ToolResult(success=False, content=f"❌ data 不是有效 JSON: {e}")

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
                return ToolResult(success=False, content="❌ data 必须是非空数组")
        return ToolResult(success=True, content=f"✅ 已写入 {n} 行到 {path}")
    except Exception as e:
        return ToolResult(success=False, content=f"❌ 写入失败: {e}")


# ─── json_read ───────────────────────────────────────────

_json_read_schema = {
    "type": "function",
    "function": {
        "name": "json_read",
        "description": "读取 JSON 或 JSONL 文件内容",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "JSON/JSONL 文件路径"},
                "encoding": {"type": "string", "description": "文件编码（默认 utf-8）"},
                "maxChars": {"type": "number", "description": "最大返回字符数（默认 50000）"},
            },
            "required": ["path"],
        },
    },
}


async def _json_read_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    path = _resolve_path(str(args["path"]), ctx)
    if not os.path.isfile(path):
        return ToolResult(success=False, content=f"❌ 文件不存在: {path}")
    encoding = str(args.get("encoding", "utf-8"))
    max_chars = int(args.get("maxChars", 50000))

    try:
        with open(path, encoding=encoding) as f:
            content = f.read()
        if path.endswith(".jsonl"):
            lines = content.strip().split("\n")
            parsed = [json.loads(line) for line in lines if line.strip()]
            formatted = json.dumps(parsed, ensure_ascii=False, indent=2)
        else:
            parsed = json.loads(content)
            formatted = json.dumps(parsed, ensure_ascii=False, indent=2)
        if len(formatted) > max_chars:
            formatted = formatted[:max_chars] + "\n... (已截断)"
        return ToolResult(success=True, content=formatted)
    except json.JSONDecodeError as e:
        return ToolResult(success=False, content=f"❌ JSON 解析失败: {e}")
    except Exception as e:
        return ToolResult(success=False, content=f"❌ 读取失败: {e}")


# ─── json_write ──────────────────────────────────────────

_json_write_schema = {
    "type": "function",
    "function": {
        "name": "json_write",
        "description": "将数据写入 JSON 文件",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "输出文件路径"},
                "data": {"type": "string", "description": "要写入的 JSON 字符串"},
                "pretty": {"type": "boolean", "description": "是否美化输出（缩进 2 空格，默认 true）"},
            },
            "required": ["path", "data"],
        },
    },
}


async def _json_write_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    path = _resolve_path(str(args["path"]), ctx)
    pretty = args.get("pretty", True) in (True, "true", "1")
    raw_data = str(args.get("data", ""))
    try:
        parsed = json.loads(raw_data)
    except json.JSONDecodeError as e:
        return ToolResult(success=False, content=f"❌ data 不是有效 JSON: {e}")

    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        indent = 2 if pretty else None
        with open(path, "w", encoding="utf-8") as f:
            json.dump(parsed, f, ensure_ascii=False, indent=indent)
        size = os.path.getsize(path)
        return ToolResult(success=True, content=f"✅ 已写入 JSON 到 {path}（{size} 字节）")
    except Exception as e:
        return ToolResult(success=False, content=f"❌ 写入失败: {e}")


# ─── 导出 ────────────────────────────────────────────────

data_tools: dict[str, ToolDefinition] = {
    "read_csv": ToolDefinition(
        schema=_read_csv_schema,
        handler=_read_csv_handler,
        permission="sandbox",
        help_text="读取 CSV/TSV 文件",
        toolbox="file_read",
    ),
    "write_csv": ToolDefinition(
        schema=_write_csv_schema,
        handler=_write_csv_handler,
        permission="sandbox",
        help_text="将数据写入 CSV 文件",
        toolbox="file_write",
    ),
    "json_read": ToolDefinition(
        schema=_json_read_schema,
        handler=_json_read_handler,
        permission="sandbox",
        help_text="读取 JSON/JSONL 文件",
        toolbox="file_read",
    ),
    "json_write": ToolDefinition(
        schema=_json_write_schema,
        handler=_json_write_handler,
        permission="sandbox",
        help_text="写入 JSON 文件",
        toolbox="file_write",
    ),
}

__all__ = ["data_tools"]
