"""飞书 IM 消息资源下载（file / image），带简单重试。

依赖可选包 ``lark-oapi``（``pip install miniagent-python[feishu]``）；凭证来自 ``FeishuConfig``。
"""

from __future__ import annotations

import asyncio
import os
from typing import Literal

from miniagent.infrastructure.logger import get_logger

_logger = get_logger(__name__)

ResourceType = Literal["file", "image"]


async def download_message_resource(
    app_id: str,
    app_secret: str,
    *,
    message_id: str,
    file_key: str,
    type_: ResourceType,
    max_attempts: int = 3,
) -> tuple[bytes, str | None]:
    """下载消息中的资源文件，返回 (二进制内容, 服务端建议的文件名)。"""
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import GetMessageResourceRequest

    client = lark.Client.builder().app_id(app_id).app_secret(app_secret).build()
    request = (
        GetMessageResourceRequest.builder()
        .message_id(message_id)
        .file_key(file_key)
        .type(type_)
        .build()
    )

    last_err: Exception | None = None
    for attempt in range(max_attempts):
        try:
            resp = await client.im.v1.message_resource.aget(request)
            if resp.success() and resp.file is not None:
                raw = resp.file.read()
                name = getattr(resp, "file_name", None) or None
                return raw, name
            msg = getattr(resp, "msg", None) or "unknown"
            code = getattr(resp, "code", None)
            raise RuntimeError(f"Feishu resource API failed: code={code} msg={msg}")
        except Exception as e:
            last_err = e
            _logger.warning(
                "下载飞书资源失败 (attempt %s/%s): %s",
                attempt + 1,
                max_attempts,
                e,
            )
            if attempt + 1 < max_attempts:
                await asyncio.sleep(0.4 * (2**attempt))
    assert last_err is not None
    raise last_err


def sanitize_filename(name: str, fallback: str = "file") -> str:
    """去掉路径分隔与非法字符，避免写入会话目录外。"""
    base = (name or "").strip() or fallback
    base = base.replace("\\", "_").replace("/", "_").replace("\x00", "")
    base = os.path.basename(base)
    return base or fallback


__all__ = [
    "ResourceType",
    "download_message_resource",
    "sanitize_filename",
]
