"""飞书 IM 素材上传与 file/image 消息发送（依赖可选 ``lark-oapi``）。"""

from __future__ import annotations

import io
import json
import os
from typing import Literal

from miniagent.feishu.lark_response import format_lark_response_error
from miniagent.feishu.types import FeishuConfig
from miniagent.infrastructure.logger import get_logger

_logger = get_logger(__name__)


def _guess_im_file_type(file_name: str) -> str:
    """飞书 ``im/v1/files`` 的 ``file_type`` 粗分类；未知时用 ``stream``。"""
    ext = (os.path.splitext(file_name)[1] or "").lower().lstrip(".")
    if ext in ("pdf",):
        return "pdf"
    if ext in ("doc", "docx"):
        return "doc"
    if ext in ("xls", "xlsx"):
        return "xls"
    if ext in ("ppt", "pptx"):
        return "ppt"
    if ext in ("png", "jpg", "jpeg", "gif", "webp", "bmp"):
        return "stream"
    return "stream"


def upload_im_file(
    config: FeishuConfig,
    data: bytes,
    *,
    file_name: str,
    file_type: str | None = None,
) -> str:
    """上传文件到 IM，返回 ``file_key``。"""
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import CreateFileRequest, CreateFileRequestBody

    ft = (file_type or _guess_im_file_type(file_name)).strip() or "stream"
    client = lark.Client.builder().app_id(config.app_id).app_secret(config.app_secret).build()
    bio = io.BytesIO(data)
    body = CreateFileRequestBody.builder().file_type(ft).file_name(file_name).file(bio).build()
    request = CreateFileRequest.builder().request_body(body).build()
    resp = client.im.v1.file.create(request)
    if not resp.success() or not resp.data or not getattr(resp.data, "file_key", None):
        raise RuntimeError(f"Feishu upload file failed: {format_lark_response_error(resp)}")
    return str(resp.data.file_key)


def upload_im_image(config: FeishuConfig, data: bytes, *, image_type: str = "message") -> str:
    """上传图片到 IM，返回 ``image_key``。"""
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import CreateImageRequest, CreateImageRequestBody

    client = lark.Client.builder().app_id(config.app_id).app_secret(config.app_secret).build()
    bio = io.BytesIO(data)
    body = CreateImageRequestBody.builder().image_type(image_type).image(bio).build()
    request = CreateImageRequest.builder().request_body(body).build()
    resp = client.im.v1.image.create(request)
    if not resp.success() or not resp.data or not getattr(resp.data, "image_key", None):
        raise RuntimeError(f"Feishu upload image failed: {format_lark_response_error(resp)}")
    return str(resp.data.image_key)


def _post_im_message(
    config: FeishuConfig,
    *,
    receive_id: str,
    msg_type: Literal["text", "file", "image", "interactive"],
    content_json: str,
    reply_to_message_id: str | None = None,
    reply_in_thread: bool = False,
    receive_id_type: str | None = None,
) -> tuple[bool, str | None]:
    """封装 ``post_im_message``，仅返回成功与否与错误文案（不含 message_id）。"""
    from miniagent.feishu.im_send import post_im_message

    ok, _mid, err = post_im_message(
        config,
        receive_id=receive_id,
        msg_type=msg_type,
        content_json=content_json,
        reply_to_message_id=reply_to_message_id,
        reply_in_thread=reply_in_thread,
        receive_id_type=receive_id_type,
    )
    return ok, err


def send_im_file_message(
    config: FeishuConfig,
    receive_id: str,
    file_key: str,
    *,
    file_name: str = "file.bin",
    reply_to_message_id: str | None = None,
    reply_in_thread: bool = False,
    receive_id_type: str | None = None,
) -> tuple[bool, str | None]:
    """发送 ``msg_type=file`` 消息。返回 ``(success, error_detail)``。"""
    payload = json.dumps({"file_key": file_key, "file_name": file_name}, ensure_ascii=False)
    return _post_im_message(
        config,
        receive_id=receive_id,
        msg_type="file",
        content_json=payload,
        reply_to_message_id=reply_to_message_id,
        reply_in_thread=reply_in_thread,
        receive_id_type=receive_id_type,
    )


def send_im_image_message(
    config: FeishuConfig,
    receive_id: str,
    image_key: str,
    *,
    reply_to_message_id: str | None = None,
    reply_in_thread: bool = False,
    receive_id_type: str | None = None,
) -> tuple[bool, str | None]:
    """发送 ``msg_type=image`` 消息。返回 ``(success, error_detail)``。"""
    payload = json.dumps({"image_key": image_key}, ensure_ascii=False)
    return _post_im_message(
        config,
        receive_id=receive_id,
        msg_type="image",
        content_json=payload,
        reply_to_message_id=reply_to_message_id,
        reply_in_thread=reply_in_thread,
        receive_id_type=receive_id_type,
    )


def delete_im_message(config: FeishuConfig, message_id: str) -> tuple[bool, str]:
    """撤回/删除一条已发送消息。返回 ``(success, error_detail)``。"""
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import DeleteMessageRequest

    client = lark.Client.builder().app_id(config.app_id).app_secret(config.app_secret).build()
    req = DeleteMessageRequest.builder().message_id(message_id).build()
    resp = client.im.v1.message.delete(req)
    if resp.success():
        return True, ""
    return False, format_lark_response_error(resp)
