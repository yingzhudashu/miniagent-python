"""云盘扩展：搜索、权限、复制移动。"""

from __future__ import annotations

import os
from typing import Any

from miniagent.feishu.lark_client import build_client
from miniagent.feishu.lark_response import format_lark_response_error
from miniagent.feishu.types import FeishuConfig


def user_access_token_from_env() -> str | None:
    tok = (os.environ.get("MINIAGENT_FEISHU_USER_ACCESS_TOKEN") or "").strip()
    return tok or None


class SearchRequiresUserTokenError(Exception):
    """云文档搜索缺少用户 OAuth token。"""

    requires_user_token = True

    def __init__(
        self,
        message: str,
        *,
        code: int | None = None,
        msg: str | None = None,
        log_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.api_msg = msg
        self.log_id = log_id

    def to_payload(self) -> dict[str, Any]:
        return {
            "ok": False,
            "requires_user_token": True,
            "hint": "配置环境变量 MINIAGENT_FEISHU_USER_ACCESS_TOKEN（用户 OAuth access token）",
            "message": str(self),
            "code": self.code,
            "msg": self.api_msg,
            "log_id": self.log_id,
        }


class SearchApiError(Exception):
    """搜索 HTTP/API 失败。"""

    def __init__(
        self,
        message: str,
        *,
        code: int | None = None,
        msg: str | None = None,
        log_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.api_msg = msg
        self.log_id = log_id

    def to_payload(self) -> dict[str, Any]:
        return {
            "ok": False,
            "requires_user_token": False,
            "message": str(self),
            "code": self.code,
            "msg": self.api_msg,
            "log_id": self.log_id,
        }


def search_docs(
    config: FeishuConfig,
    query: str,
    *,
    user_token: str | None = None,
    page_size: int = 20,
) -> list[dict[str, Any]]:
    """云文档搜索（需用户 access token；SDK 版本差异大，走 HTTP 探针）。"""
    ut = user_token or user_access_token_from_env()
    if not ut:
        raise SearchRequiresUserTokenError(
            "search 需要 MINIAGENT_FEISHU_USER_ACCESS_TOKEN（用户 OAuth token）"
        )
    from miniagent.feishu.drive_client import _http_post_json

    body = {
        "search_key": query,
        "count": min(page_size, 50),
        "docs_types": ["doc", "docx", "sheet", "bitable"],
    }
    raw = _http_post_json(
        "https://open.feishu.cn/open-apis/suite/docs-api/search/object",
        body,
        headers={
            "Authorization": f"Bearer {ut}",
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    code = raw.get("code")
    if code not in (0, None):
        try:
            icode = int(code)
        except (TypeError, ValueError):
            icode = None
        raise SearchApiError(
            f"search failed: code={code} msg={raw.get('msg')}",
            code=icode,
            msg=str(raw.get("msg") or ""),
            log_id=str(raw.get("log_id") or "") or None,
        )
    data = raw.get("data") or {}
    items = data.get("docs_entities") or data.get("items") or []
    out: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict):
            out.append(
                {
                    "title": str(item.get("title") or ""),
                    "url": str(item.get("url") or ""),
                    "token": str(item.get("docs_token") or item.get("token") or ""),
                }
            )
    return out


def list_permissions(
    config: FeishuConfig, file_token: str, *, doc_type: str = "docx"
) -> list[dict]:
    from lark_oapi.api.drive.v1 import ListPermissionMemberRequest

    client = build_client(config)
    req = ListPermissionMemberRequest.builder().token(file_token).type(doc_type).build()
    resp = client.drive.v1.permission_member.list(req)
    if not resp.success() or not resp.data:
        raise RuntimeError(f"list_permissions failed: {format_lark_response_error(resp)}")
    items = []
    for m in getattr(resp.data, "items", None) or []:
        items.append(
            {
                "member_type": str(getattr(m, "member_type", None) or ""),
                "member_id": str(getattr(m, "member_id", None) or ""),
                "perm": str(getattr(m, "perm", None) or ""),
            }
        )
    return items


def copy_file(
    config: FeishuConfig, file_token: str, *, name: str, folder_token: str, doc_type: str = "docx"
) -> str:
    from lark_oapi.api.drive.v1 import CopyFileRequest, CopyFileRequestBody

    client = build_client(config)
    body = (
        CopyFileRequestBody.builder().name(name).folder_token(folder_token).type(doc_type).build()
    )
    resp = client.drive.v1.file.copy(
        CopyFileRequest.builder().file_token(file_token).request_body(body).build()
    )
    if not resp.success() or not resp.data or not resp.data.file:
        raise RuntimeError(f"copy failed: {format_lark_response_error(resp)}")
    return str(resp.data.file.token or "")


def add_permission(
    config: FeishuConfig,
    file_token: str,
    *,
    member_type: str,
    member_id: str,
    perm: str = "view",
    doc_type: str = "docx",
    need_notification: bool = False,
) -> dict[str, Any]:
    from lark_oapi.api.drive.v1 import BaseMember, CreatePermissionMemberRequest

    client = build_client(config)
    body = BaseMember.builder().member_type(member_type).member_id(member_id).perm(perm).build()
    req = (
        CreatePermissionMemberRequest.builder()
        .token(file_token)
        .type(doc_type)
        .need_notification(need_notification)
        .request_body(body)
        .build()
    )
    resp = client.drive.v1.permission_member.create(req)
    if not resp.success():
        raise RuntimeError(f"add_permission failed: {format_lark_response_error(resp)}")
    m = getattr(resp.data, "member", None) if resp.data else None
    return {
        "member_type": str(getattr(m, "member_type", None) or member_type),
        "member_id": str(getattr(m, "member_id", None) or member_id),
        "perm": str(getattr(m, "perm", None) or perm),
    }


def remove_permission(
    config: FeishuConfig,
    file_token: str,
    *,
    member_type: str,
    member_id: str,
    doc_type: str = "docx",
) -> None:
    from lark_oapi.api.drive.v1 import DeletePermissionMemberRequest

    client = build_client(config)
    req = (
        DeletePermissionMemberRequest.builder()
        .token(file_token)
        .type(doc_type)
        .member_type(member_type)
        .member_id(member_id)
        .build()
    )
    resp = client.drive.v1.permission_member.delete(req)
    if not resp.success():
        raise RuntimeError(f"remove_permission failed: {format_lark_response_error(resp)}")


def move_file(
    config: FeishuConfig, file_token: str, *, folder_token: str, doc_type: str = "docx"
) -> None:
    from lark_oapi.api.drive.v1 import MoveFileRequest, MoveFileRequestBody

    client = build_client(config)
    body = MoveFileRequestBody.builder().type(doc_type).folder_token(folder_token).build()
    resp = client.drive.v1.file.move(
        MoveFileRequest.builder().file_token(file_token).request_body(body).build()
    )
    if not resp.success():
        raise RuntimeError(f"move failed: {format_lark_response_error(resp)}")


__all__ = [
    "SearchApiError",
    "SearchRequiresUserTokenError",
    "add_permission",
    "copy_file",
    "list_permissions",
    "move_file",
    "remove_permission",
    "search_docs",
    "user_access_token_from_env",
]
