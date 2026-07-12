"""Mini Agent Python — 视觉理解工具

提供图片分析工具：
- analyze_image: 分析图片内容，生成描述

依赖：
- miniagent.feishu.vision_desc: describe_image 函数
- miniagent.security.sandbox: 路径沙箱保护
- ToolContext.llm_client: 组合根显式注入的 AsyncOpenAI 客户端

支持的图片格式：PNG、JPG、JPEG、GIF、WebP、BMP（最大 20MB）。

重构说明：使用 ToolBuilder 简化工具定义。
"""

from __future__ import annotations

import os
from typing import Any

from miniagent.core.constants import FEISHU_VISION_MAX_BYTES
from miniagent.infrastructure.json_config import get_config
from miniagent.tools.base import tool
from miniagent.tools.path_utils import resolve_path_for_tool
from miniagent.types.error_prefix import ERROR_PREFIX
from miniagent.types.tool import ToolContext, ToolDefinition, ToolResult

# ════════════════════════════════════════════════════════
# Handler
# ════════════════════════════════════════════════════════


async def _analyze_image_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """分析图片内容。

    使用 LLM 视觉能力分析图片，生成描述文本。
    """
    image_path, path_err = resolve_path_for_tool(str(args["path"]), ctx)
    if path_err:
        return path_err

    if not os.path.isfile(image_path):
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 图片文件不存在: {args['path']}")

    size = os.path.getsize(image_path)
    max_bytes = FEISHU_VISION_MAX_BYTES
    if size > max_bytes:
        return ToolResult(
            success=False,
            content=f"{ERROR_PREFIX} 图片文件过大 ({size // 1024 // 1024}MB)，上限 20MB",
        )

    client = ctx.llm_client
    if client is None:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} LLM 客户端未注入")

    model = get_config("model.model", "")
    if not model:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 未配置模型(model.model)")

    prompt = str(args.get("prompt", "") or "请简洁描述这张图片的内容，不超过 150 字。")

    from miniagent.feishu.vision_desc import describe_image

    description = await describe_image(
        file_path=image_path,
        client=client,
        model=model,
        prompt=prompt,
    )

    if not description:
        return ToolResult(
            success=False,
            content=f"{ERROR_PREFIX} 图片分析失败（可能是模型不支持视觉理解，请确认 model.model 配置）",
        )

    return ToolResult(
        success=True,
        content=f"📷 图片分析结果:\n{description}",
        meta={"file_path": args["path"], "prompt": prompt},
    )


# ════════════════════════════════════════════════════════
# Tool Definition (使用 ToolBuilder)
# ════════════════════════════════════════════════════════

vision_tools: dict[str, ToolDefinition] = {
    "analyze_image": tool("analyze_image", "分析图片内容并生成描述。支持 PNG、JPG、JPEG、GIF、WebP、BMP 格式，最大 20MB。")
        .param("path", "string", "图片文件路径（相对于工作区或绝对路径）")
        .optional("prompt", "string", "分析提示词（可选），用于定制分析角度")
        .sandbox()
        .toolbox("vision")
        .handler(_analyze_image_handler)
        .build(),
}

__all__ = ["vision_tools"]
