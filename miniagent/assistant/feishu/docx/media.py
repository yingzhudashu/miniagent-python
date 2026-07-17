"""云文档图片/文件块与素材上传。"""

from __future__ import annotations

import os
from typing import Any

from miniagent.assistant.feishu.docx.blocks import _find_page_block_id, batch_update_blocks
from miniagent.assistant.feishu.lark_client import build_client
from miniagent.assistant.feishu.lark_response import format_lark_response_error
from miniagent.ui.feishu.types import FeishuConfig


def upload_drive_media(
    config: FeishuConfig,
    data: bytes,
    *,
    file_name: str,
    parent_type: str,
    parent_node: str,
) -> str:
    """上传素材到云盘/文档上下文，返回 file_token 或 image_key。"""
    from lark_oapi.api.drive.v1 import UploadAllMediaRequest, UploadAllMediaRequestBody

    client = build_client(config)
    body = (
        UploadAllMediaRequestBody.builder()
        .file_name(file_name)
        .parent_type(parent_type)
        .parent_node(parent_node)
        .size(len(data))
        .file(data)
        .build()
    )
    req = UploadAllMediaRequest.builder().request_body(body).build()
    resp = client.drive.v1.media.upload_all(req)
    if not resp.success() or not resp.data:
        raise RuntimeError(f"media upload failed: {format_lark_response_error(resp)}")
    token = str(
        getattr(resp.data, "file_token", None) or getattr(resp.data, "image_key", None) or ""
    )
    if not token:
        raise RuntimeError("media upload: empty file_token")
    return token


def insert_image_block(
    config: FeishuConfig,
    document_id: str,
    file_token: str,
    *,
    parent_block_id: str | None = None,
    index: int | None = None,
) -> None:
    """在文档中插入图片块。"""
    client = build_client(config)
    parent = parent_block_id or _find_page_block_id(client, document_id)
    req_body: dict[str, Any] = {
        "block_id": parent,
        "insert_image": {"image_token": file_token},
    }
    if index is not None:
        req_body["insert_image"]["index"] = index
    batch_update_blocks(config, document_id, [req_body])


def upload_doc_image_from_bytes(
    config: FeishuConfig,
    document_id: str,
    data: bytes,
    *,
    file_name: str = "image.png",
    parent_block_id: str | None = None,
    index: int | None = None,
) -> str:
    """上传图片字节并插入文档，返回 image_token。"""
    token = upload_drive_media(
        config,
        data,
        file_name=file_name,
        parent_type="docx_image",
        parent_node=document_id,
    )
    insert_image_block(config, document_id, token, parent_block_id=parent_block_id, index=index)
    return token


def upload_doc_image_from_path(
    config: FeishuConfig,
    document_id: str,
    path: str,
    **kwargs: Any,
) -> str:
    """从本地路径读取图片并插入文档。"""
    with open(path, "rb") as f:
        data = f.read()
    name = os.path.basename(path) or "image.png"
    return upload_doc_image_from_bytes(config, document_id, data, file_name=name, **kwargs)


def download_media_bytes(
    config: FeishuConfig,
    file_token: str,
    *,
    extra: str | None = None,
) -> bytes:
    """按 file_token 下载云盘/文档素材二进制内容。"""
    from lark_oapi.api.drive.v1 import DownloadMediaRequest

    client = build_client(config)
    b = DownloadMediaRequest.builder().file_token(file_token)
    if extra:
        # ``extra`` 是飞书 SDK 请求字段，不涉及数据库查询。
        b = b.extra(extra)  # nosec B610
    resp = client.drive.v1.media.download(b.build())
    if not resp.success():
        raise RuntimeError(f"media download failed: {format_lark_response_error(resp)}")
    raw = getattr(resp, "file", None) or getattr(resp, "raw", None)
    if raw is None and resp.data is not None:
        raw = getattr(resp.data, "file", None)
    if isinstance(raw, bytes):
        return raw
    if isinstance(raw, (bytearray, memoryview)):
        return bytes(raw)
    raise RuntimeError("media download: empty body")


def upload_doc_file_from_path(
    config: FeishuConfig,
    document_id: str,
    path: str,
    *,
    filename: str | None = None,
) -> str:
    """从本地路径上传文件到文档上下文，返回 file_token。"""
    with open(path, "rb") as f:
        data = f.read()
    name = filename or os.path.basename(path) or "file.bin"
    return upload_drive_media(
        config,
        data,
        file_name=name,
        parent_type="docx_file",
        parent_node=document_id,
    )


__all__ = [
    "download_media_bytes",
    "insert_image_block",
    "upload_doc_file_from_path",
    "upload_doc_image_from_bytes",
    "upload_doc_image_from_path",
    "upload_drive_media",
]
