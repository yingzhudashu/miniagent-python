"""Feishu docx v1 document API."""

from __future__ import annotations

from miniagent.feishu.lark_client import build_client
from miniagent.feishu.lark_response import format_lark_response_error
from miniagent.feishu.types import FeishuConfig


def create_document(config: FeishuConfig, *, folder_token: str, title: str) -> tuple[str, int]:
    """创建飞书云文档。

    Args:
        config: 飞书配置（包含 app_id、app_secret）
        folder_token: 目标文件夹 token
        title: 文档标题

    Returns:
        (document_id, revision_id) 文档 ID 和初始版本号

    Raises:
        RuntimeError: 创建失败或返回数据为空
    """
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


def get_document(config: FeishuConfig, document_id: str) -> dict[str, Any]:
    """获取飞书云文档元信息。

    Args:
        config: 飞书配置
        document_id: 文档 ID

    Returns:
        包含 document_id、title、revision_id 的字典

    Raises:
        RuntimeError: 获取失败或返回数据为空
    """
    from typing import Any
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
    """获取飞书云文档原始 Markdown 内容。

    Args:
        config: 飞书配置
        document_id: 文档 ID

    Returns:
        文档原始 Markdown 内容

    Raises:
        RuntimeError: 获取失败或返回数据为空
    """
    from lark_oapi.api.docx.v1 import RawContentDocumentRequest

    client = build_client(config)
    resp = client.docx.v1.document.raw_content(
        RawContentDocumentRequest.builder().document_id(document_id).build()
    )
    if not resp.success() or not resp.data:
        raise RuntimeError(f"Feishu docx raw_content failed: {format_lark_response_error(resp)}")
    return str(getattr(resp.data, "content", None) or "")


def delete_document(config: FeishuConfig, file_token: str) -> None:
    """删除飞书云文档。

    Args:
        config: 飞书配置
        file_token: 文档 file token（与 document_id 不同）

    Raises:
        RuntimeError: 删除失败
    """
    from lark_oapi.api.drive.v1 import DeleteFileRequest

    client = build_client(config)
    resp = client.drive.v1.file.delete(DeleteFileRequest.builder().file_token(file_token).build())
    if not resp.success():
        raise RuntimeError(f"Feishu drive delete failed: {format_lark_response_error(resp)}")
