"""CLI 下将 Assistant 的 Markdown 回复渲染为终端样式（可选 Rich）。

依赖 ``pip install -e ".[cli]"``；原始 Markdown 模式见 ``MINIAGENT_CLI_RAW_MARKDOWN``。
"""

from __future__ import annotations

import os
import re
from io import StringIO

_STRIP_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def cli_raw_markdown_enabled() -> bool:
    """为 ``1``/``true``/``yes`` 时关闭渲染，保留原始 Markdown（便于复制或调试）。"""
    v = os.environ.get("MINIAGENT_CLI_RAW_MARKDOWN", "").strip().lower()
    return v in ("1", "true", "yes")


def render_markdown_to_ansi(markdown: str, *, width: int, justify: str = "left") -> str | None:
    """将 Markdown 转为带 ANSI 序列的文本；不可用或未安装 Rich 时返回 ``None``。

    使用 ``color_system=\"standard\"``，以便 ``prompt_toolkit.formatted_text.ANSI`` 稳定解析。

    Args:
        markdown: Markdown 文本
        width: 渲染宽度
        justify: 对齐方式，默认 "left"（靠左对齐），可选 "center"、"full"
    """
    if cli_raw_markdown_enabled():
        return None
    try:
        from rich.console import Console
        from rich.markdown import Markdown
    except ImportError:
        return None
    w = max(20, int(width))
    buf = StringIO()
    console = Console(
        file=buf,
        width=w,
        force_terminal=True,
        color_system="standard",
        highlight=False,
    )
    console.print(Markdown(markdown or "", justify=justify))
    return buf.getvalue()


def strip_ansi(text: str) -> str:
    """去掉 ANSI 转义，用于剪贴板等纯文本场景。"""
    return _STRIP_ANSI.sub("", text)


__all__ = [
    "cli_raw_markdown_enabled",
    "render_markdown_to_ansi",
    "strip_ansi",
]
