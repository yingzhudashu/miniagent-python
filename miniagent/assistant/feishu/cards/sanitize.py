"""卡片抽取文本的安全清理（CWE-117 等）。"""

from __future__ import annotations

import re

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize_card_text(text: str, *, max_len: int = 32_000) -> str:
    """清理卡片 Markdown：截断长度、去除控制字符与日志注入风险字符。"""
    s = _CTRL.sub("", text or "")
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    if len(s) > max_len:
        s = s[:max_len] + "…"
    return s


__all__ = ["sanitize_card_text"]
