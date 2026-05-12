"""飞书云文档 docx v1 最小封装：创建文档、读取 Markdown 原文（依赖 ``lark-oapi``）。"""

from __future__ import annotations

from miniagent.feishu.lark_response import format_lark_response_error
from miniagent.feishu.types import FeishuConfig
from miniagent.infrastructure.logger import get_logger

_logger = get_logger(__name__)


def create_document(config: FeishuConfig, *, folder_token: str, title: str) -> tuple[str, int]:
    """在指定云盘文件夹下创建空白云文档。

    Returns:
        ``(document_id, revision_id)``
    """
    import lark_oapi as lark
    from lark_oapi.api.docx.v1 import CreateDocumentRequest, CreateDocumentRequestBody

    client = lark.Client.builder().app_id(config.app_id).app_secret(config.app_secret).build()
    body = CreateDocumentRequestBody.builder().folder_token(folder_token).title(title).build()
    req = CreateDocumentRequest.builder().request_body(body).build()
    resp = client.docx.v1.document.create(req)
    if not resp.success() or not resp.data or not resp.data.document:
        raise RuntimeError(f"Feishu docx create failed: {format_lark_response_error(resp)}")
    doc = resp.data.document
    did = str(doc.document_id or "")
    rid = int(doc.revision_id or 0)
    if not did:
        raise RuntimeError("Feishu docx create: empty document_id")
    return did, rid


def get_document_raw_content(config: FeishuConfig, document_id: str) -> str:
    """读取文档 Markdown 原文（开放平台 raw_content 接口）。"""
    import lark_oapi as lark
    from lark_oapi.api.docx.v1 import RawContentDocumentRequest

    client = lark.Client.builder().app_id(config.app_id).app_secret(config.app_secret).build()
    req = RawContentDocumentRequest.builder().document_id(document_id).build()
    resp = client.docx.v1.document.raw_content(req)
    if not resp.success() or not resp.data:
        raise RuntimeError(f"Feishu docx raw_content failed: {format_lark_response_error(resp)}")
    return str(getattr(resp.data, "content", None) or "")
