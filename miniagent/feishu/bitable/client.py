"""飞书 Bitable v1 API 封装（应用元数据、字段、记录 CRUD）。"""

from __future__ import annotations

import json
from typing import Any

from miniagent.feishu.lark_client import build_client
from miniagent.feishu.lark_response import format_lark_response_error
from miniagent.feishu.types import FeishuConfig

_LIST_RECORDS_MAX = 500


def _fields_to_dict(fields: Any) -> dict[str, Any]:
    if fields is None:
        return {}
    if isinstance(fields, dict):
        return dict(fields)
    out: dict[str, Any] = {}
    if hasattr(fields, "__iter__"):
        for item in fields:
            name = getattr(item, "field_name", None) or getattr(item, "name", None)
            if name:
                out[str(name)] = getattr(item, "value", item)
    return out


def get_app_meta(config: FeishuConfig, app_token: str) -> dict[str, Any]:
    from lark_oapi.api.bitable.v1 import GetAppRequest

    client = build_client(config)
    resp = client.bitable.v1.app.get(GetAppRequest.builder().app_token(app_token).build())
    if not resp.success() or not resp.data or not resp.data.app:
        raise RuntimeError(f"Feishu bitable app.get failed: {format_lark_response_error(resp)}")
    app = resp.data.app
    return {
        "app_token": str(getattr(app, "app_token", None) or app_token),
        "name": str(getattr(app, "name", None) or ""),
        "url": str(getattr(app, "url", None) or ""),
    }


def list_tables(config: FeishuConfig, app_token: str, *, page_token: str | None = None) -> tuple[list[dict], str | None, bool]:
    from lark_oapi.api.bitable.v1 import ListAppTableRequest

    client = build_client(config)
    b = ListAppTableRequest.builder().app_token(app_token).page_size(100)
    if page_token:
        b = b.page_token(page_token)
    resp = client.bitable.v1.app_table.list(b.build())
    if not resp.success() or not resp.data:
        raise RuntimeError(f"Feishu bitable table.list failed: {format_lark_response_error(resp)}")
    items = []
    for t in getattr(resp.data, "items", None) or []:
        items.append(
            {
                "table_id": str(getattr(t, "table_id", None) or ""),
                "name": str(getattr(t, "name", None) or ""),
                "revision": int(getattr(t, "revision", None) or 0),
            }
        )
    nxt = getattr(resp.data, "page_token", None)
    return items, str(nxt) if nxt else None, bool(getattr(resp.data, "has_more", False))


def list_fields(config: FeishuConfig, app_token: str, table_id: str, *, page_token: str | None = None) -> tuple[list[dict], str | None, bool]:
    from lark_oapi.api.bitable.v1 import ListAppTableFieldRequest

    client = build_client(config)
    b = ListAppTableFieldRequest.builder().app_token(app_token).table_id(table_id).page_size(100)
    if page_token:
        b = b.page_token(page_token)
    resp = client.bitable.v1.app_table_field.list(b.build())
    if not resp.success() or not resp.data:
        raise RuntimeError(f"Feishu bitable field.list failed: {format_lark_response_error(resp)}")
    items = []
    for f in getattr(resp.data, "items", None) or []:
        items.append(
            {
                "field_id": str(getattr(f, "field_id", None) or ""),
                "field_name": str(getattr(f, "field_name", None) or ""),
                "type": int(getattr(f, "type", None) or 0),
                "is_primary": bool(getattr(f, "is_primary", False)),
            }
        )
    nxt = getattr(resp.data, "page_token", None)
    return items, str(nxt) if nxt else None, bool(getattr(resp.data, "has_more", False))


def list_records(
    config: FeishuConfig,
    app_token: str,
    table_id: str,
    *,
    page_token: str | None = None,
    page_size: int = 100,
    view_id: str | None = None,
    field_names: list[str] | None = None,
    filter_expr: str | None = None,
    sort: list[str] | None = None,
) -> tuple[list[dict], str | None, bool]:
    from lark_oapi.api.bitable.v1 import SearchAppTableRecordRequest, SearchAppTableRecordRequestBody

    client = build_client(config)
    body_b = SearchAppTableRecordRequestBody.builder()
    if view_id:
        body_b = body_b.view_id(view_id)
    if field_names:
        body_b = body_b.field_names(field_names)
    if filter_expr:
        body_b = body_b.filter(filter_expr)
    if sort:
        body_b = body_b.sort(sort)
    body = body_b.build()
    b = (
        SearchAppTableRecordRequest.builder()
        .app_token(app_token)
        .table_id(table_id)
        .page_size(min(page_size, 500))
        .request_body(body)
    )
    if page_token:
        b = b.page_token(page_token)
    resp = client.bitable.v1.app_table_record.search(b.build())
    if not resp.success() or not resp.data:
        raise RuntimeError(f"Feishu bitable record.search failed: {format_lark_response_error(resp)}")
    items = []
    for rec in getattr(resp.data, "items", None) or []:
        items.append(
            {
                "record_id": str(getattr(rec, "record_id", None) or ""),
                "fields": _fields_to_dict(getattr(rec, "fields", None)),
            }
        )
        if len(items) >= _LIST_RECORDS_MAX:
            break
    nxt = getattr(resp.data, "page_token", None)
    return items, str(nxt) if nxt else None, bool(getattr(resp.data, "has_more", False))


def get_record(config: FeishuConfig, app_token: str, table_id: str, record_id: str) -> dict[str, Any]:
    from lark_oapi.api.bitable.v1 import GetAppTableRecordRequest

    client = build_client(config)
    resp = client.bitable.v1.app_table_record.get(
        GetAppTableRecordRequest.builder().app_token(app_token).table_id(table_id).record_id(record_id).build()
    )
    if not resp.success() or not resp.data or not resp.data.record:
        raise RuntimeError(f"Feishu bitable record.get failed: {format_lark_response_error(resp)}")
    rec = resp.data.record
    return {
        "record_id": str(getattr(rec, "record_id", None) or record_id),
        "fields": _fields_to_dict(getattr(rec, "fields", None)),
    }


def create_record(config: FeishuConfig, app_token: str, table_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    from lark_oapi.api.bitable.v1 import AppTableRecord, CreateAppTableRecordRequest

    client = build_client(config)
    record = AppTableRecord.builder().fields(fields).build()
    resp = client.bitable.v1.app_table_record.create(
        CreateAppTableRecordRequest.builder().app_token(app_token).table_id(table_id).request_body(record).build()
    )
    if not resp.success() or not resp.data or not resp.data.record:
        raise RuntimeError(f"Feishu bitable record.create failed: {format_lark_response_error(resp)}")
    rec = resp.data.record
    return {
        "record_id": str(getattr(rec, "record_id", None) or ""),
        "fields": _fields_to_dict(getattr(rec, "fields", None)),
    }


def update_record(
    config: FeishuConfig, app_token: str, table_id: str, record_id: str, fields: dict[str, Any]
) -> dict[str, Any]:
    from lark_oapi.api.bitable.v1 import AppTableRecord, UpdateAppTableRecordRequest

    client = build_client(config)
    record = AppTableRecord.builder().fields(fields).build()
    resp = client.bitable.v1.app_table_record.update(
        UpdateAppTableRecordRequest.builder()
        .app_token(app_token)
        .table_id(table_id)
        .record_id(record_id)
        .request_body(record)
        .build()
    )
    if not resp.success() or not resp.data or not resp.data.record:
        raise RuntimeError(f"Feishu bitable record.update failed: {format_lark_response_error(resp)}")
    rec = resp.data.record
    return {
        "record_id": str(getattr(rec, "record_id", None) or record_id),
        "fields": _fields_to_dict(getattr(rec, "fields", None)),
    }


def delete_record(config: FeishuConfig, app_token: str, table_id: str, record_id: str) -> None:
    from lark_oapi.api.bitable.v1 import DeleteAppTableRecordRequest

    client = build_client(config)
    resp = client.bitable.v1.app_table_record.delete(
        DeleteAppTableRecordRequest.builder().app_token(app_token).table_id(table_id).record_id(record_id).build()
    )
    if not resp.success():
        raise RuntimeError(f"Feishu bitable record.delete failed: {format_lark_response_error(resp)}")


def upload_record_attachment(
    config: FeishuConfig,
    app_token: str,
    table_id: str,
    record_id: str,
    field_name: str,
    file_bytes: bytes,
    *,
    file_name: str = "attachment.bin",
) -> dict[str, Any]:
    """上传文件并写入记录的附件字段（需字段类型为附件）。"""
    from miniagent.feishu.docx.media import upload_drive_media

    token = upload_drive_media(
        config,
        file_bytes,
        file_name=file_name,
        parent_type="bitable_file",
        parent_node=app_token,
    )
    return update_record(
        config,
        app_token,
        table_id,
        record_id,
        {field_name: [{"file_token": token}]},
    )


def delete_records_batch(config: FeishuConfig, app_token: str, table_id: str, record_ids: list[str]) -> int:
    from lark_oapi.api.bitable.v1 import BatchDeleteAppTableRecordRequest, BatchDeleteAppTableRecordRequestBody

    if not record_ids:
        return 0
    ids = record_ids[:500]
    client = build_client(config)
    body = BatchDeleteAppTableRecordRequestBody.builder().records(ids).build()
    resp = client.bitable.v1.app_table_record.batch_delete(
        BatchDeleteAppTableRecordRequest.builder().app_token(app_token).table_id(table_id).request_body(body).build()
    )
    if not resp.success():
        raise RuntimeError(f"Feishu bitable batch_delete failed: {format_lark_response_error(resp)}")
    return len(ids)
