"""CLI 下将 Assistant 的 Markdown 回复渲染为终端样式（可选 Rich）。

依赖 ``pip install -e ".[cli]"``；原始 Markdown 模式见 ``MINIAGENT_CLI_RAW_MARKDOWN``。

重要：Rich Markdown 标题默认居中（Heading 类硬编码 text.justify = "center"），
本模块通过自定义渲染强制标题左对齐。
"""

from __future__ import annotations

import os
import re
from io import StringIO
from typing import Any

_STRIP_ANSI = re.compile(r"\x1b\[[0-9;]*m")

# 标题正则：匹配 # 开头的行
_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+)$")


def cli_raw_markdown_enabled() -> bool:
    """为 ``1``/``true``/``yes`` 时关闭渲染，保留原始 Markdown（便于复制或调试）。"""
    v = os.environ.get("MINIAGENT_CLI_RAW_MARKDOWN", "").strip().lower()
    return v in ("1", "true", "yes")


def render_markdown_to_ansi(markdown: str, *, width: int, justify: str = "left") -> str | None:
    """将 Markdown 转为带 ANSI 序列的文本；标题强制左对齐。

    Rich Markdown 的 Heading 类硬编码 text.justify = "center"，忽略父级 justify 参数。
    本函数通过分段渲染解决：标题单独渲染（左对齐），其他内容用 Rich Markdown 渲染。

    Args:
        markdown: Markdown 文本
        width: 渲染宽度
        justify: 正文对齐方式（标题强制左对齐）

    Returns:
        ANSI 格式文本，或 None（Rich 未安装或 raw markdown 模式）
    """
    if cli_raw_markdown_enabled():
        return None

    try:
        from rich.console import Console
        from rich.markdown import Markdown
        from rich.text import Text
        from rich.panel import Panel
        from rich import box
        from rich.style import Style
    except ImportError:
        return None

    w = max(20, int(width))

    # 分段渲染：识别标题行，单独处理
    lines = markdown.split("\n")
    output_parts: list[str] = []

    i = 0
    while i < len(lines):
        line = lines[i]

        # 检查是否为标题
        heading_match = _HEADING_PATTERN.match(line)

        if heading_match:
            # 渲染标题（强制左对齐）
            level = len(heading_match.group(1))
            text = heading_match.group(2).strip()

            heading_ansi = _render_heading_left_aligned(level, text, width=w)
            output_parts.append(heading_ansi)
            i += 1
        else:
            # 收集非标题内容块
            block_lines: list[str] = []
            while i < len(lines) and not _HEADING_PATTERN.match(lines[i]):
                block_lines.append(lines[i])
                i += 1

            block = "\n".join(block_lines)
            if block.strip():
                # 渲染非标题块（使用 Rich Markdown，传入 justify 参数）
                buf = StringIO()
                console = Console(
                    file=buf,
                    width=w,
                    force_terminal=True,
                    color_system="standard",
                    highlight=False,
                )
                console.print(Markdown(block, justify=justify))
                output_parts.append(buf.getvalue())

    return "".join(output_parts)


def _render_heading_left_aligned(level: int, text: str, width: int) -> str:
    """渲染标题（强制左对齐）。

    Args:
        level: 标题级别（1-6）
        text: 标题文本
        width: 渲染宽度

    Returns:
        ANSI 格式标题文本
    """
    try:
        from rich.console import Console
        from rich.text import Text
        from rich.panel import Panel
        from rich import box
        from rich.style import Style
    except ImportError:
        # 回退：简单文本
        prefix = "#" * level
        return f"{prefix} {text}\n"

    buf = StringIO()
    console = Console(
        file=buf,
        width=width,
        force_terminal=True,
        color_system="standard",
        highlight=False,
    )

    # 创建左对齐的 Text 对象（关键：justify="left"）
    heading_text = Text(text, justify="left")

    if level == 1:
        # H1: 使用 Panel（边框），内部文本左对齐
        # 注意：Panel 本身可能居中，但内部 Text 左对齐
        # 使用 box.HEAVY 保持 Rich 默认风格
        panel = Panel(
            heading_text,
            box=box.HEAVY,
            style=Style(color="bright_blue", bold=True),
            expand=False,  # 不扩展到全宽，保持紧凑
        )
        console.print(panel)
    elif level == 2:
        # H2: 加粗 + 下划线风格（左对齐）
        heading_text.stylize(Style(bold=True, underline=True, color="bright_green"))
        console.print("")  # 前空行
        console.print(heading_text)
        console.print("")  # 后空行
    elif level == 3:
        # H3: 加粗风格（左对齐）
        heading_text.stylize(Style(bold=True, color="yellow"))
        console.print(heading_text)
    elif level == 4:
        # H4: 加粗 + dim（左对齐）
        heading_text.stylize(Style(bold=True, dim=True))
        console.print(heading_text)
    elif level == 5:
        # H5: dim + italic（左对齐）
        heading_text.stylize(Style(dim=True, italic=True))
        console.print(heading_text)
    else:
        # H6: 最小风格（左对齐）
        heading_text.stylize(Style(dim=True))
        console.print(heading_text)

    return buf.getvalue()


def strip_ansi(text: str) -> str:
    """去掉 ANSI 转义，用于剪贴板等纯文本场景。"""
    return _STRIP_ANSI.sub("", text)


__all__ = [
    "cli_raw_markdown_enabled",
    "render_markdown_to_ansi",
    "strip_ansi",
]