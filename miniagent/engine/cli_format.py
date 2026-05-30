"""CLI Format — CLI transcript 格式化工具。

从 main.py 拆分，负责 CLI transcript 中用户消息块和回复块的格式化。

职责：
- 用户消息块格式化（含飞书渠道标识）
- 回复块格式化（含 Markdown 渲染）
- 动态宽度适应终端大小

使用方式：
    from miniagent.engine.cli_format import format_cli_user_block, format_cli_reply_block
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from miniagent.engine.utils import get_render_width


def format_cli_user_block(
    append_fn: Callable[[str, str], None] | None,
    prompt: str,
    stick_bottom: list[bool],
    *,
    channel_label: str | None = None,
) -> None:
    """Write user block to CLI transcript with optional channel identifier.

    边框宽度动态适应终端大小，避免溢出。

    Args:
        append_fn: transcript append 函数（写入样式和文本）
        prompt: 用户输入文本
        stick_bottom: 底部粘滞状态（用于全屏 CLI）
        channel_label: 渠道标识（如 "飞书私聊"）
    """
    if append_fn is None or not prompt:
        return

    width = get_render_width()

    stick_bottom[0] = True
    append_fn("class:cli-spacer", "\n")
    append_fn("class:cli-border-strong", "═" * width + "\n")
    if channel_label:
        append_fn("class:cli-user-title", f"You · [{channel_label}]\n")
    else:
        append_fn("class:cli-user-title", "You\n")
    append_fn("class:cli-border", "─" * width + "\n")
    for line in (prompt or "").splitlines() or [""]:
        append_fn("class:cli-user-body", line + "\n")
    append_fn("class:cli-spacer", "\n")


def format_cli_reply_block(
    append_fn: Callable[..., None] | None,
    append_ansi_fn: Callable[[Any], None] | None,
    text: str,
) -> None:
    """Write assistant reply block to CLI transcript with Markdown rendering.

    边框宽度动态适应终端大小，Markdown 渲染宽度跟随调整。

    Args:
        append_fn: transcript append 函数（写入样式和文本）
        append_ansi_fn: ANSI transcript append 函数（写入 ANSI 格式化文本）
        text: Assistant 回复文本（Markdown）
    """
    if append_fn is None or not text:
        return
    from prompt_toolkit.formatted_text import ANSI

    from miniagent.engine.markdown_cli import render_markdown_to_ansi

    width = get_render_width()

    append_fn("class:cli-spacer", "\n")
    append_fn("class:cli-border", chr(0x2500) * width + "\n")
    append_fn("class:cli-assistant-title", "Assistant\n")
    append_fn("class:cli-border", chr(0x2500) * width + "\n")
    body = (text or "").strip()
    if body:
        try:
            # Markdown 渲染宽度：使用终端宽度减边距
            md_w = max(40, width - 4)
            ansi_body = render_markdown_to_ansi(body, width=md_w)
            if ansi_body and ansi_body.strip():
                body_lines = ansi_body.rstrip("\n").split("\n")
                transcript_body = "\n".join(ln if ln else "" for ln in body_lines) + "\n"
                ansi_obj = ANSI(transcript_body)
                ansi_obj._source_md = body  # type: ignore[attr-defined]
                if append_ansi_fn:
                    append_ansi_fn(ansi_obj)
                else:
                    for line in body.splitlines() or [""]:
                        append_fn("class:cli-assistant-body", line + "\n")
        except Exception:
            for line in body.splitlines() or [""]:
                append_fn("class:cli-assistant-body", line + "\n")
    append_fn("class:cli-spacer", "\n")
    append_fn("class:cli-border-strong", chr(0x2550) * width + "\n")


__all__ = ["format_cli_user_block", "format_cli_reply_block"]