"""Feishu docx v1 document API."""

from __future__ import annotations

from miniagent.feishu.lark_client import build_client
from miniagent.feishu.lark_response import format_lark_response_error
from miniagent.feishu.types import FeishuConfig


def create_document(config: FeishuConfig, *, folder_token: str, title: str) -> tuple[str, int]:
    from lark_oapi.api.docx.v1 import CreateDocumentRequest, CreateDocumentRequestBody

    client = build_client(config)
    body = CreateDocumentRequestBody.builder().folder_token(folder_token).title(title).build()
    resp = client.docx.v1.document.create(
        CreateDocumentRequest.builder().request_body(body).build()
    )
    if not resp.success() or not resp.data or not resp.data.document:
        raise RuntimeError(f"Feishu docx create failed: {format_lark_response_error(resp)}")
    doc = resp.data.document
    did = str(doc.document_id or "")
    rid = int(doc.revision_id or 0)
    if not did:
        raise RuntimeError("Feishu docx create: empty document_id")
    return did, rid


def get_document(config: FeishuConfig, document_id: str) -> dict:
    from lark_oapi.api.docx.v1 import GetDocumentRequest

    client = build_client(config)
    resp = client.docx.v1.document.get(
        GetDocumentRequest.builder().document_id(document_id).build()
    )
    if not resp.success() or not resp.data or not resp.data.document:
        raise RuntimeError(f"Feishu docx get failed: {format_lark_response_error(resp)}")
    d = resp.data.document
    return {
        "document_id": str(getattr(d, "document_id", None) or document_id),
        "title": str(getattr(d, "title", None) or ""),
        "revision_id": int(getattr(d, "revision_id", None) or 0),
    }


def get_document_raw_content(config: FeishuConfig, document_id: str) -> str:
    from lark_oapi.api.docx.v1 import RawContentDocumentRequest

    client = build_client(config)
    resp = client.docx.v1.document.raw_content(
        RawContentDocumentRequest.builder().document_id(document_id).build()
    )
    if not resp.success() or not resp.data:
        raise RuntimeError(f"Feishu docx raw_content failed: {format_lark_response_error(resp)}")
    return str(getattr(resp.data, "content", None) or "")


def delete_document(config: FeishuConfig, file_token: str) -> None:
    from lark_oapi.api.drive.v1 import DeleteFileRequest

    client = build_client(config)
    resp = client.drive.v1.file.delete(DeleteFileRequest.builder().file_token(file_token).build())
    if not resp.success():
        raise RuntimeError(f"Feishu drive delete failed: {format_lark_response_error(resp)}")
