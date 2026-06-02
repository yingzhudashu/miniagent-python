"""Mini Agent Python — 视觉理解工具

提供图片分析工具：
- analyze_image: 分析图片内容，生成描述

依赖：
- miniagent.feishu.vision_desc: describe_image 函数
- miniagent.security.sandbox: 路径沙箱保护
- miniagent.core.openai_client: 共享 AsyncOpenAI 客户端

支持的图片格式：PNG、JPG、JPEG、GIF、WebP、BMP（最大 20MB）。
"""

from __future__ import annotations

import os
from typing import Any

from miniagent.infrastructure.json_config import get_config

from miniagent.core.openai_client import get_shared_async_openai

from miniagent.tools._path_utils import resolve_path_from_ctx
from miniagent.types.error_prefix import ERROR_PREFIX
from miniagent.types.tool import ToolContext, ToolDefinition, ToolResult


# ════════════════════════════════════════════════════════
# analyze_image
# ════════════════════════════════════════════════════════

_analyze_image_schema = {
    "type": "function",
    "function": {
        "name": "analyze_image",
        "description": "分析图片内容并生成描述。支持 PNG、JPG、JPEG、GIF、WebP、BMP 格式，最大 20MB。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "图片文件路径（相对于工作区或绝对路径）",
                },
                "prompt": {
                    "type": "string",
                    "description": "分析提示词（可选），用于定制分析角度，如 '识别图中的文字' 或 '描述图中人物的动作'",
                },
            },
            "required": ["path"],
        },
    },
}


async def _analyze_image_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """分析图片内容。

    Args:
        args: 包含 path（图片路径）、prompt（可选分析提示词）
        ctx: 工具执行上下文

    Returns:
        ToolResult: 成功时包含图片描述；失败时返回错误信息
    """
    # 1. 解析并验证路径（沙箱保护）
    try:
        image_path = resolve_path_from_ctx(str(args["path"]), ctx)
    except ValueError as e:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 路径越权: {e}")

    # 2. 检查文件存在性
    if not os.path.isfile(image_path):
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 图片文件不存在: {args['path']}")

    # 3. 检查文件大小（复用 vision_desc 的限制）
    size = os.path.getsize(image_path)
    max_bytes = 20 * 1024 * 1024
    if size > max_bytes:
        return ToolResult(
            success=False,
            content=f"{ERROR_PREFIX} 图片文件过大 ({size // 1024 // 1024}MB)，上限 20MB",
        )

    # 4. 获取 OpenAI 客户端
    try:
        client = get_shared_async_openai()
    except RuntimeError as e:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} LLM 客户端未配置: {e}")

    # 5. 获取模型配置（从JSON配置读取）
    model = get_config("model.model", "")
    if not model:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 未配置模型(model.model)")

    # 6. 分析提示词
    prompt = str(args.get("prompt", "") or "请简洁描述这张图片的内容，不超过 150 字。")

    # 7. 调用 describe_image
    from miniagent.feishu.vision_desc import describe_image

    description = await describe_image(
        file_path=image_path,
        client=client,
        model=model,
        prompt=prompt,
    )

    if not description:
        # describe_image 返回空字符串表示模型不支持视觉或调用失败
        return ToolResult(
            success=False,
            content=f"{ERROR_PREFIX} 图片分析失败（可能是模型不支持视觉理解，请确认 OPENAI_MODEL 配置）",
        )

    return ToolResult(
        success=True,
        content=f"📷 图片分析结果:\n{description}",
        meta={"file_path": args["path"], "prompt": prompt},
    )


# ════════════════════════════════════════════════════════
# 导出
# ════════════════════════════════════════════════════════

vision_tools: dict[str, ToolDefinition] = {
    "analyze_image": ToolDefinition(
        schema=_analyze_image_schema,
        handler=_analyze_image_handler,
        permission="sandbox",
        help_text="分析图片内容",
        toolbox="vision",
    ),
}

__all__ = ["vision_tools"]