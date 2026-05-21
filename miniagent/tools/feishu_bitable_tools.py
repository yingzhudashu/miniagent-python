"""飞书多维表格聚合工具 ``feishu_bitable``（7 种 action）。"""

from __future__ import annotations

import json
import os
from typing import Any

from miniagent.feishu.bitable.client import (
    create_record,
    delete_record,
    delete_records_batch,
    get_app_meta,
    get_record,
    list_fields,
    list_records,
    list_tables,
    update_record,
    upload_record_attachment,
)
from miniagent.feishu.lark_client import config_from_env, require_lark_oapi
from miniagent.feishu.token_resolve import extract_bitable_app_token, extract_table_id
from miniagent.feishu.types import FeishuConfig
from miniagent.types.tool import ToolContext, ToolDefinition, ToolResult

FEISHU_BITABLE_TOOL_NAMES = frozenset({"feishu_bitable"})

_SUPPORTED_ACTIONS = (
    "get_meta",
    "list_fields",
    "list_records",
    "get_record",
    "create_record",
    "update_record",
    "delete_record",
    "upload_attachment",
)


def _fmt_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _resolve_under_workspace(workspace: str, rel: str) -> str:
    base = os.path.realpath(workspace)
    tail = (rel or "").strip().replace("\\", "/").lstrip("/")
    cand = os.path.realpath(os.path.join(base, tail))
    if cand != base and not cand.startswith(base + os.sep):
        raise ValueError("路径越出会话工作区")
    return cand


def _parse_fields_arg(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return {}
        return json.loads(s)
    return None


async def _feishu_bitable(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    _ = ctx
    action = str(args.get("action") or "").strip().lower()
    if action not in _SUPPORTED_ACTIONS:
        return ToolResult(
            success=False,
            content=f"⚠️ 未知 action={action!r}。支持: {', '.join(_SUPPORTED_ACTIONS)}",
        )

    cfg = config_from_env()
    if cfg is None:
        return ToolResult(success=False, content="⚠️ 未配置 FEISHU_APP_ID / FEISHU_APP_SECRET。")
    try:
        require_lark_oapi()
    except ImportError:
        return ToolResult(success=False, content="⚠️ 请安装 lark-oapi（pip install miniagent-python[feishu]）。")

    app_token = extract_bitable_app_token(str(args.get("app_token") or ""))
    url_hint = str(args.get("app_url") or args.get("url") or "")
    if not app_token and url_hint:
        app_token = extract_bitable_app_token(url_hint)
    table_id = extract_table_id(str(args.get("table_id") or ""), url_hint=url_hint or app_token)

    try:
        if action == "get_meta":
            if not app_token:
                return ToolResult(success=False, content="⚠️ 需要 app_token 或 base URL。")
            meta = get_app_meta(cfg, app_token)
            tables, nxt, has_more = list_tables(cfg, app_token)
            return ToolResult(
                success=True,
                content=_fmt_json({"app": meta, "tables": tables, "has_more": has_more, "page_token": nxt}),
            )
        if not app_token or not table_id:
            return ToolResult(success=False, content="⚠️ 需要 app_token 与 table_id（或 base URL 含 ?table=）。")

        if action == "list_fields":
            items, nxt, has_more = list_fields(
                cfg, app_token, table_id, page_token=str(args.get("page_token") or "").strip() or None
            )
            return ToolResult(
                success=True,
                content=_fmt_json({"fields": items, "has_more": has_more, "page_token": nxt}),
            )
        if action == "list_records":
            field_names = args.get("field_names")
            fn_list = None
            if isinstance(field_names, list):
                fn_list = [str(x) for x in field_names]
            elif isinstance(field_names, str) and field_names.strip():
                fn_list = [x.strip() for x in field_names.split(",") if x.strip()]
            items, nxt, has_more = list_records(
                cfg,
                app_token,
                table_id,
                page_token=str(args.get("page_token") or "").strip() or None,
                page_size=int(args.get("page_size") or 100),
                view_id=str(args.get("view_id") or "").strip() or None,
                field_names=fn_list,
                filter_expr=str(args.get("filter") or args.get("filter_expr") or "").strip() or None,
                sort=args.get("sort") if isinstance(args.get("sort"), list) else None,
            )
            return ToolResult(
                success=True,
                content=_fmt_json({"records": items, "has_more": has_more, "page_token": nxt}),
            )
        if action == "get_record":
            rid = str(args.get("record_id") or "").strip()
            if not rid:
                return ToolResult(success=False, content="⚠️ 需要 record_id。")
            return ToolResult(success=True, content=_fmt_json(get_record(cfg, app_token, table_id, rid)))
        if action == "create_record":
            fields = _parse_fields_arg(args.get("fields"))
            if fields is None:
                return ToolResult(success=False, content="⚠️ 需要 fields（对象或 JSON 字符串）。")
            return ToolResult(
                success=True,
                content=_fmt_json(create_record(cfg, app_token, table_id, fields)),
            )
        if action == "update_record":
            rid = str(args.get("record_id") or "").strip()
            fields = _parse_fields_arg(args.get("fields"))
            if not rid:
                return ToolResult(success=False, content="⚠️ 需要 record_id。")
            if fields is None:
                return ToolResult(success=False, content="⚠️ 需要 fields。")
            return ToolResult(
                success=True,
                content=_fmt_json(update_record(cfg, app_token, table_id, rid, fields)),
            )
        if action == "delete_record":
            rid = str(args.get("record_id") or "").strip()
            rids = args.get("record_ids")
            if isinstance(rids, list) and rids:
                n = delete_records_batch(cfg, app_token, table_id, [str(x) for x in rids])
                return ToolResult(success=True, content=f"✅ 已批量删除 {n} 条记录。")
            if not rid:
                return ToolResult(success=False, content="⚠️ 需要 record_id 或 record_ids 数组。")
            delete_record(cfg, app_token, table_id, rid)
            return ToolResult(success=True, content=f"✅ 已删除记录 {rid}。")
        if action == "upload_attachment":
            rid = str(args.get("record_id") or "").strip()
            field_name = str(args.get("field_name") or "").strip()
            rel = str(args.get("relative_path") or "").strip()
            ws = (ctx.cwd or "").strip()
            if not rid or not field_name or not rel or not ws:
                return ToolResult(success=False, content="⚠️ 需要 record_id、field_name、relative_path。")
            path = _resolve_under_workspace(ws, rel)
            with open(path, "rb") as f:
                data = f.read()
            out = upload_record_attachment(
                cfg,
                app_token,
                table_id,
                rid,
                field_name,
                data,
                file_name=os.path.basename(path),
            )
            return ToolResult(success=True, content=_fmt_json(out))
    except json.JSONDecodeError as e:
        return ToolResult(success=False, content=f"⚠️ fields JSON 无效: {e}")
    except Exception as e:
        return ToolResult(success=False, content=f"⚠️ feishu_bitable.{action} 失败: {e}")

    return ToolResult(success=False, content="⚠️ 未处理的 action。")


_feishu_bitable_schema = {
    "type": "function",
    "function": {
        "name": "feishu_bitable",
        "description": (
            "飞书多维表格（Bitable）统一工具。action："
            "get_meta（应用+表列表）、list_fields、list_records、get_record、"
            "create_record、update_record、delete_record（支持 record_ids 批量≤500）、"
            "upload_attachment（会话工作区文件写入附件字段）。"
            "app_token 可为 token 或 https://.../base/APP_TOKEN URL；table_id 可来自 URL ?table=。"
            "写记录前建议先 list_fields。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(_SUPPORTED_ACTIONS)},
                "app_token": {"type": "string"},
                "app_url": {"type": "string", "description": "多维表格分享链接"},
                "table_id": {"type": "string"},
                "record_id": {"type": "string"},
                "record_ids": {"type": "array", "items": {"type": "string"}},
                "fields": {"description": "字段名→值 的对象或 JSON 字符串"},
                "page_token": {"type": "string"},
                "page_size": {"type": "integer"},
                "view_id": {"type": "string"},
                "field_names": {"description": "逗号分隔或字符串数组，限定返回列"},
                "filter": {"type": "string", "description": "筛选表达式"},
                "filter_expr": {"type": "string"},
                "sort": {"type": "array", "items": {"type": "string"}},
                "field_name": {"type": "string", "description": "upload_attachment 附件字段名"},
                "relative_path": {
                    "type": "string",
                    "description": "upload_attachment 会话工作区内文件路径",
                },
            },
            "required": ["action"],
        },
    },
}

feishu_bitable_tools: dict[str, ToolDefinition] = {
    "feishu_bitable": ToolDefinition(
        schema=_feishu_bitable_schema,
        handler=_feishu_bitable,
        permission="allowlist",
        help_text="飞书多维表格记录与字段管理",
        toolbox=None,
    ),
}

__all__ = ["FEISHU_BITABLE_TOOL_NAMES", "feishu_bitable_tools"]
