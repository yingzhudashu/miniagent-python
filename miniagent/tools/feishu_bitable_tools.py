"""飞书多维表格聚合工具 ``feishu_bitable``（7 种 action）。

**重构说明**：配置检查使用 miniagent/tools/feishu_utils.py 的共享函数。
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from miniagent.feishu._utils import fmt_json, resolve_under_workspace
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
from miniagent.feishu.lark_client import config_from_env
from miniagent.feishu.token_resolve import extract_bitable_app_token, extract_table_id
from miniagent.tools.feishu_utils import check_lark_oapi
from miniagent.types.error_prefix import SUCCESS_PREFIX, WARNING_PREFIX
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


def _parse_fields_arg(raw: Any) -> dict[str, Any] | None:
    """解析 fields 参数（支持 dict、JSON 字符串、None）。"""
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


def _bitable_get_meta(
    cfg: Any, app_token: str, args: dict[str, Any], ctx: ToolContext
) -> ToolResult:
    """读取应用元数据与表列表。"""
    del args, ctx
    if not app_token:
        return ToolResult(success=False, content=f"{WARNING_PREFIX} 需要 app_token 或 base URL。")
    meta = get_app_meta(cfg, app_token)
    tables, token, has_more = list_tables(cfg, app_token)
    return ToolResult(
        success=True,
        content=fmt_json(
            {"app": meta, "tables": tables, "has_more": has_more, "page_token": token}
        ),
    )


def _field_names(raw: Any) -> list[str] | None:
    """规范化字段名数组或逗号分隔字符串。"""
    if isinstance(raw, list):
        return [str(item) for item in raw]
    if isinstance(raw, str) and raw.strip():
        return [item.strip() for item in raw.split(",") if item.strip()]
    return None


def _bitable_list(
    cfg: Any,
    app_token: str,
    table_id: str,
    args: dict[str, Any],
    *,
    records: bool,
) -> ToolResult:
    """列出字段或记录，并返回统一分页信息。"""
    page_token = str(args.get("page_token") or "").strip() or None
    if not records:
        items, next_token, has_more = list_fields(cfg, app_token, table_id, page_token=page_token)
        key = "fields"
    else:
        items, next_token, has_more = list_records(
            cfg,
            app_token,
            table_id,
            page_token=page_token,
            page_size=int(args.get("page_size") or 100),
            view_id=str(args.get("view_id") or "").strip() or None,
            field_names=_field_names(args.get("field_names")),
            filter_expr=str(args.get("filter") or args.get("filter_expr") or "").strip() or None,
            sort=args.get("sort") if isinstance(args.get("sort"), list) else None,
        )
        key = "records"
    return ToolResult(
        success=True,
        content=fmt_json({key: items, "has_more": has_more, "page_token": next_token}),
    )


def _bitable_record(
    action: str,
    cfg: Any,
    app_token: str,
    table_id: str,
    args: dict[str, Any],
) -> ToolResult:
    """读取、创建或更新单条记录。"""
    record_id = str(args.get("record_id") or "").strip()
    if action == "get_record":
        if not record_id:
            return ToolResult(success=False, content=f"{WARNING_PREFIX} 需要 record_id。")
        value = get_record(cfg, app_token, table_id, record_id)
    else:
        fields = _parse_fields_arg(args.get("fields"))
        if fields is None:
            return ToolResult(success=False, content=f"{WARNING_PREFIX} 需要 fields。")
        if action == "create_record":
            value = create_record(cfg, app_token, table_id, fields)
        elif record_id:
            value = update_record(cfg, app_token, table_id, record_id, fields)
        else:
            return ToolResult(success=False, content=f"{WARNING_PREFIX} 需要 record_id。")
    return ToolResult(success=True, content=fmt_json(value))


def _bitable_delete(cfg: Any, app_token: str, table_id: str, args: dict[str, Any]) -> ToolResult:
    """删除单条或批量记录。"""
    record_ids = args.get("record_ids")
    if isinstance(record_ids, list) and record_ids:
        count = delete_records_batch(cfg, app_token, table_id, [str(item) for item in record_ids])
        return ToolResult(success=True, content=f"{SUCCESS_PREFIX} 已批量删除 {count} 条记录。")
    record_id = str(args.get("record_id") or "").strip()
    if not record_id:
        return ToolResult(
            success=False, content=f"{WARNING_PREFIX} 需要 record_id 或 record_ids 数组。"
        )
    delete_record(cfg, app_token, table_id, record_id)
    return ToolResult(success=True, content=f"{SUCCESS_PREFIX} 已删除记录 {record_id}。")


def _bitable_upload(
    cfg: Any,
    app_token: str,
    table_id: str,
    args: dict[str, Any],
    ctx: ToolContext,
) -> ToolResult:
    """上传工作区文件到附件字段。"""
    record_id = str(args.get("record_id") or "").strip()
    field_name = str(args.get("field_name") or "").strip()
    relative_path = str(args.get("relative_path") or "").strip()
    workspace = (ctx.cwd or "").strip()
    if not record_id or not field_name or not relative_path or not workspace:
        return ToolResult(
            success=False,
            content=f"{WARNING_PREFIX} 需要 record_id、field_name、relative_path。",
        )
    path = resolve_under_workspace(workspace, relative_path)
    with open(path, "rb") as stream:
        data = stream.read()
    output = upload_record_attachment(
        cfg,
        app_token,
        table_id,
        record_id,
        field_name,
        data,
        file_name=os.path.basename(path),
    )
    return ToolResult(success=True, content=fmt_json(output))


def _dispatch_bitable_action(
    action: str,
    cfg: Any,
    app_token: str,
    table_id: str,
    args: dict[str, Any],
    ctx: ToolContext,
) -> ToolResult:
    """分派已通过 token 校验的 Bitable action。"""
    if action == "list_fields":
        return _bitable_list(cfg, app_token, table_id, args, records=False)
    if action == "list_records":
        return _bitable_list(cfg, app_token, table_id, args, records=True)
    if action in {"get_record", "create_record", "update_record"}:
        return _bitable_record(action, cfg, app_token, table_id, args)
    if action == "delete_record":
        return _bitable_delete(cfg, app_token, table_id, args)
    if action == "upload_attachment":
        return _bitable_upload(cfg, app_token, table_id, args, ctx)
    return ToolResult(success=False, content=f"{WARNING_PREFIX} 未处理的 action。")


def _feishu_bitable_sync(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """在线程内执行 Bitable SDK 调用并统一映射工具结果。"""
    action = str(args.get("action") or "").strip().lower()
    if action not in _SUPPORTED_ACTIONS:
        return ToolResult(
            success=False,
            content=f"{WARNING_PREFIX} 未知 action={action!r}。支持: {', '.join(_SUPPORTED_ACTIONS)}",
        )

    cfg = config_from_env()
    if cfg is None:
        return ToolResult(
            success=False, content=f"{WARNING_PREFIX} 未配置 FEISHU_APP_ID / FEISHU_APP_SECRET。"
        )
    dep_err = check_lark_oapi()
    if dep_err:
        return dep_err

    app_token = extract_bitable_app_token(str(args.get("app_token") or ""))
    url_hint = str(args.get("app_url") or args.get("url") or "")
    if not app_token and url_hint:
        app_token = extract_bitable_app_token(url_hint)
    table_id = extract_table_id(str(args.get("table_id") or ""), url_hint=url_hint or app_token)

    try:
        if action == "get_meta":
            return _bitable_get_meta(cfg, app_token, args, ctx)
        if not app_token or not table_id:
            return ToolResult(
                success=False,
                content=f"{WARNING_PREFIX} 需要 app_token 与 table_id（或 base URL 含 ?table=）。",
            )

        return _dispatch_bitable_action(action, cfg, app_token, table_id, args, ctx)
    except json.JSONDecodeError as e:
        return ToolResult(success=False, content=f"{WARNING_PREFIX} fields JSON 无效: {e}")
    except Exception as e:
        return ToolResult(
            success=False, content=f"{WARNING_PREFIX} feishu_bitable.{action} 失败: {e}"
        )


async def _feishu_bitable(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Run synchronous lark-oapi Bitable calls outside the event loop."""
    return await asyncio.to_thread(_feishu_bitable_sync, args, ctx)


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
        toolbox="feishu",
    ),
}

__all__ = ["FEISHU_BITABLE_TOOL_NAMES", "feishu_bitable_tools"]
