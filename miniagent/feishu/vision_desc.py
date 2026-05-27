"""飞书图片视觉描述：调用用户配置的模型生成图片内容描述。"""

from __future__ import annotations

import base64
import logging
import os

from openai import AsyncOpenAI

_logger = logging.getLogger(__name__)

# 图片大小上限（20MB）
_MAX_BYTES = 20 * 1024 * 1024

# 模型不支持视觉理解时的典型错误关键词
_VISION_UNSUPPORTED_KEYWORDS = (
    "does not support",
    "image_url",
    "invalid_request_error",
    "modality",
)


def _image_mime_hint(file_path: str) -> str:
    """根据扩展名猜测 MIME 类型，降级为 jpeg。"""
    ext = os.path.splitext(file_path)[1].lower()
    _MAP = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
    }
    return _MAP.get(ext, "image/jpeg")


async def describe_image(
    file_path: str,
    client: AsyncOpenAI,
    model: str,
    *,
    prompt: str = "请简洁描述这张图片的内容，不超过 150 字。",
) -> str:
    """调用配置的模型生成图片描述。如果模型不支持视觉理解，返回空字符串。

    Args:
        file_path: 图片文件路径
        client: OpenAI 异步客户端
        model: 要使用的模型名（用户配置的 OPENAI_MODEL）
        prompt: 描述提示词

    Returns:
        图片描述文本，失败时返回空字符串。
    """
    try:
        size = os.path.getsize(file_path)
        if size > _MAX_BYTES:
            _logger.debug("跳过图片描述：文件过大 %s (%d bytes)", file_path, size)
            return ""

        with open(file_path, "rb") as f:
            raw = f.read()

        mime = _image_mime_hint(file_path)
        b64 = base64.b64encode(raw).decode("ascii")
        data_uri = f"data:{mime};base64,{b64}"

        resp = await client.chat.completions.create(
            model=model,
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
            max_tokens=500,
        )
        text = (resp.choices[0].message.content or "").strip()
        return text
    except Exception as e:
        err_str = str(e).lower()
        if any(kw in err_str for kw in _VISION_UNSUPPORTED_KEYWORDS):
            _logger.info("模型 %s 不支持视觉理解: %s", model, e)
            return ""
        _logger.warning("图片描述生成失败: %s", e)
        return ""
