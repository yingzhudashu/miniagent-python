"""CLI Format — CLI transcript 格式化工具。

从 main.py 拆分，负责 CLI transcript 中用户消息块和回复块的格式化。

职责：
- 用户消息块格式化（含飞书渠道标识）
- 回复块格式化（含 Markdown 渲染与安全 ANSI 过滤）
- 动态宽度适应终端或全屏视口

宽度策略：
- 未传 ``render_width`` / ``markdown_width`` 时，使用 ``get_render_width()``（终端列宽）
- 全屏 prompt_toolkit 场景应由调用方传入视口宽度（``rule_line_width`` /
  ``markdown_render_width``），避免与可见区域错位

使用方式::

    from miniagent.engine.cli_format import format_cli_user_block, format_cli_reply_block
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from miniagent.core.constants import CLI_WIDTH_MARGIN
from miniagent.engine.cli_transcript import (
    is_valid_pt_style,
    markdown_render_width,
    safe_ansi_fragments,
)
from miniagent.engine.utils import get_render_width

_logger = logging.getLogger(__name__)

_BORDER_LIGHT = "\u2500"  # ─
_BORDER_STRONG = "\u2550"  # ═
_ASSISTANT_BODY_STYLE = "class:cli-assistant-body"


def _resolve_border_width(render_width: int | None) -> int:
    return render_width if render_width is not None else get_render_width()


def _resolve_markdown_width(border_width: int, markdown_width: int | None) -> int:
    if markdown_width is not None:
        return markdown_width
    return markdown_render_width(border_width, CLI_WIDTH_MARGIN)


def _append_reply_body(
    append_fn: Callable[[str, str], None],
    append_ansi_fn: Callable[[Any], None] | None,
    *,
    source_md: str,
    transcript_body: str,
) -> None:
    """写入回复正文：优先带 ``_source_md`` 的 ANSI 对象，失败则安全片段降级。"""
    safe_ft = safe_ansi_fragments(transcript_body)
    if append_ansi_fn is not None:
        try:
            from prompt_toolkit.formatted_text import ANSI, to_formatted_text

            ansi_obj = ANSI(transcript_body)
            if all(is_valid_pt_style(style) for style, _ in to_formatted_text(ansi_obj)):
                ansi_obj._source_md = source_md  # type: ignore[attr-defined]
                append_ansi_fn(ansi_obj)
                return
        except Exception as e:
            _logger.debug("ANSI 对象写入失败，降级为安全片段: %s", e)

    for style, txt in safe_ft:
        append_fn(style or _ASSISTANT_BODY_STYLE, txt)


def format_cli_user_block(
    append_fn: Callable[[str, str], None] | None,
    prompt: str,
    stick_bottom: list[bool],
    *,
    channel_label: str | None = None,
    render_width: int | None = None,
) -> None:
    """向 CLI transcript 写入用户消息块（可选渠道标识）。

    Args:
        append_fn: transcript 追加函数 ``(style_cls, text)``
        prompt: 用户输入文本
        stick_bottom: 全屏 CLI 粘底标志（可变容器 ``[bool]``，本函数会置 ``True``）
        channel_label: 渠道标识（如 ``"飞书私聊"``）
        render_width: 边框线宽度；全屏模式应传视口列宽，省略则用终端宽度
    """
    if append_fn is None or not prompt:
        return

    width = _resolve_border_width(render_width)

    stick_bottom[0] = True
    append_fn("class:cli-spacer", "\n")
    append_fn("class:cli-border-strong", _BORDER_STRONG * width + "\n")
    if channel_label:
        append_fn("class:cli-user-title", f"You · [{channel_label}]\n")
    else:
        append_fn("class:cli-user-title", "You\n")
    append_fn("class:cli-border", _BORDER_LIGHT * width + "\n")
    for line in (prompt or "").splitlines() or [""]:
        append_fn("class:cli-user-body", line + "\n")
    append_fn("class:cli-spacer", "\n")


def format_cli_reply_block(
    append_fn: Callable[..., None] | None,
    append_ansi_fn: Callable[[Any], None] | None,
    text: str,
    *,
    render_width: int | None = None,
    markdown_width: int | None = None,
) -> None:
    """向 CLI transcript 写入 Assistant 回复块（Markdown → 安全 ANSI）。

    Args:
        append_fn: transcript 追加函数
        append_ansi_fn: ANSI 对象追加函数（支持终端缩放时重渲染 ``_source_md``）
        text: Assistant 回复（Markdown）
        render_width: 边框线宽度；全屏模式应传视口列宽
        markdown_width: Markdown 渲染宽度；全屏模式应传 ``markdown_render_width(vp, margin)``
    """
    if append_fn is None or not text:
        return

    from miniagent.engine.markdown_cli import render_markdown_to_ansi

    width = _resolve_border_width(render_width)
    md_w = _resolve_markdown_width(width, markdown_width)

    append_fn("class:cli-spacer", "\n")
    append_fn("class:cli-border", _BORDER_LIGHT * width + "\n")
    append_fn("class:cli-assistant-title", "Assistant\n")
    append_fn("class:cli-border", _BORDER_LIGHT * width + "\n")
    body = (text or "").strip()
    if body:
        try:
            ansi_body = render_markdown_to_ansi(body, width=md_w, justify="left")
            if ansi_body and ansi_body.strip():
                body_lines = ansi_body.rstrip("\n").split("\n")
                transcript_body = "\n".join(ln if ln else "" for ln in body_lines) + "\n"
                _append_reply_body(
                    append_fn,
                    append_ansi_fn,
                    source_md=body,
                    transcript_body=transcript_body,
                )
            else:
                for line in body.splitlines() or [""]:
                    append_fn(_ASSISTANT_BODY_STYLE, line + "\n")
        except Exception as e:
            _logger.warning("回复块 Markdown 渲染失败，降级为纯文本: %s", e)
            for line in body.splitlines() or [""]:
                append_fn(_ASSISTANT_BODY_STYLE, line + "\n")
    append_fn("class:cli-spacer", "\n")
    append_fn("class:cli-border-strong", _BORDER_STRONG * width + "\n")


def get_cli_format_widths(state: dict[str, Any] | None) -> tuple[int | None, int | None]:
    """从 ``CliLoopState`` 读取全屏 transcript 视口宽度。

    全屏 ``run_cli_loop`` 会向 state 注册 ``cli_render_width`` /
    ``cli_markdown_width`` 回调；未设置时返回 ``(None, None)``，调用方回退终端宽度。

    Returns:
        ``(render_width, markdown_width)``
    """
    if not state:
        return None, None
    rw = state.get("cli_render_width")
    mw = state.get("cli_markdown_width")
    render_w = rw() if callable(rw) else None
    md_w = mw() if callable(mw) else None
    return render_w, md_w


__all__ = [
    "format_cli_reply_block",
    "format_cli_user_block",
    "get_cli_format_widths",
]
