"""飞书图片视觉描述：调用用户配置的模型生成图片内容描述。"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
from pathlib import Path
from typing import Any

from miniagent.agent.constants import FEISHU_VISION_MAX_BYTES
from miniagent.llm.legacy_transport import create_completion

_logger = logging.getLogger(__name__)


def _max_image_bytes() -> int:
    return FEISHU_VISION_MAX_BYTES

# 模型不支持视觉理解时的典型错误关键词
_VISION_UNSUPPORTED_KEYWORDS = (
    "does not support",
    "image_url",
    "invalid_request_error",
    "modality",
)

# 扩展名 → MIME 类型映射（未命中时降级为 jpeg）
_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}


def _image_mime_hint(file_path: str) -> str:
    """根据扩展名猜测 MIME 类型，降级为 jpeg。"""
    ext = os.path.splitext(file_path)[1].lower()
    return _MIME_BY_EXT.get(ext, "image/jpeg")


async def describe_image(
    file_path: str,
    client: Any,
    model: str,
    *,
    prompt: str = "请简洁描述这张图片的内容，不超过 150 字。",
) -> str:
    """调用配置的模型生成图片描述。如果模型不支持视觉理解，返回空字符串。

    Args:
        file_path: 图片文件路径
        client: OpenAI 异步客户端
        model: 要使用的模型名（用户配置的 MINIAGENT_MODEL_MODEL）
        prompt: 描述提示词

    Returns:
        图片描述文本，失败时返回空字符串。
    """
    try:
        size = await asyncio.to_thread(os.path.getsize, file_path)
        if size > _max_image_bytes():
            _logger.debug("跳过图片描述：文件过大 %s (%d bytes)", file_path, size)
            return ""

        raw = await asyncio.to_thread(Path(file_path).read_bytes)

        mime = _image_mime_hint(file_path)
        b64 = await asyncio.to_thread(
            lambda: base64.b64encode(raw).decode("ascii")
        )
        data_uri = f"data:{mime};base64,{b64}"

        resp = await create_completion(
            client,
            params={"model": model, "max_tokens": 500, "_role": "vision"},
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": data_uri, "detail": "low"},
                        },
                    ],
                },
            ],
        )
        return (resp.content or "").strip()
    except Exception as e:
        err_str = str(e).lower()
        if any(kw in err_str for kw in _VISION_UNSUPPORTED_KEYWORDS):
            _logger.info("模型 %s 不支持视觉理解: %s", model, e)
            return ""
        _logger.warning("图片描述生成失败: %s", e)
        return ""


__all__ = ["describe_image"]
