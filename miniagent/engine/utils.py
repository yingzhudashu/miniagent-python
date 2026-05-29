"""CLI 终端辅助工具函数。

提供统一的终端宽度计算、状态格式化等公共工具，
避免在 main.py 和 thinking.py 中重复实现。

模块职责：
- 终端宽度计算（自适应宽屏）
- 状态信息格式化
- 历史记录提取辅助

非职责：
- 不处理用户输入（属于 main.py）
- 不处理 thinking 输出（属于 thinking.py）
"""

from __future__ import annotations

import shutil
from typing import Any


# ─── 终端宽度计算 ───────────────────────────────────────────────

# 渲染宽度范围常量
MIN_RENDER_WIDTH = 40   # 最小宽度，确保基本可读
MAX_RENDER_WIDTH = 500  # 最大宽度，适应宽屏显示器
WIDTH_MARGIN = 4        # 边距（滚动条、边框等）


def get_terminal_width(fallback_width: int = 80) -> int:
    """获取终端列宽（自适应宽屏显示器）。

    Args:
        fallback_width: 获取失败时的默认宽度

    Returns:
        终端列宽（原始值，未限制范围）
    """
    try:
        return shutil.get_terminal_size(fallback=(fallback_width, 24)).columns
    except Exception:
        return fallback_width


def get_render_width(fallback_width: int = 80) -> int:
    """获取 CLI 渲染宽度（减去边距，限制范围）。

    用于 Markdown 渲染、表格显示等需要固定宽度的场景。
    计算公式：max(40, min(500, terminal_width - 4))

    Args:
        fallback_width: 获取失败时的默认宽度

    Returns:
        渲染宽度（最小 40，最大 500）
    """
    terminal_width = get_terminal_width(fallback_width)
    return max(MIN_RENDER_WIDTH, min(MAX_RENDER_WIDTH, terminal_width - WIDTH_MARGIN))


# ─── 状态格式化辅助 ───────────────────────────────────────────────

def format_duration_seconds(seconds: float) -> str:
    """格式化秒数为人类可读形式。

    Args:
        seconds: 秒数

    Returns:
        格式化字符串，如 "45.2s" 或 "2m30s"
    """
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    if minutes < 60:
        return f"{minutes}m{secs}s"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h{mins}m"


def format_file_size(size_bytes: int) -> str:
    """格式化文件大小为人类可读形式。

    Args:
        size_bytes: 字节数

    Returns:
        格式化字符串，如 "150KB" 或 "2.5MB"
    """
    if size_bytes < 1024:
        return f"{size_bytes}B"
    kb = size_bytes / 1024
    if kb < 1024:
        return f"{int(kb)}KB"
    mb = kb / 1024
    if mb < 1024:
        return f"{mb:.1f}MB"
    gb = mb / 1024
    return f"{gb:.2f}GB"


def truncate_text(text: str, max_length: int, suffix: str = "...") -> str:
    """截断文本到指定长度。

    Args:
        text: 原始文本
        max_length: 最大长度
        suffix: 截断后缀

    Returns:
        截断后的文本
    """
    if len(text) <= max_length:
        return text
    return text[:max_length - len(suffix)] + suffix


# ─── 会话历史辅助 ───────────────────────────────────────────────

def extract_last_qa_from_history(history: list[dict[str, Any]]) -> tuple[str, str] | None:
    """从历史记录中提取最后一轮问答。

    Args:
        history: 历史消息列表

    Returns:
        (用户问题, Agent回复) 元组，或 None
    """
    if not history:
        return None

    user_msg = None
    assistant_msg = None

    # 从后向前查找
    for msg in reversed(history):
        role = msg.get("role", "")
        if role == "assistant" and assistant_msg is None:
            assistant_msg = msg.get("content", "")
        elif role == "user" and user_msg is None:
            user_msg = msg.get("content", "")
            break  # 找到用户消息后停止

    if user_msg and assistant_msg:
        return (user_msg, assistant_msg)
    return None


__all__ = [
    "get_terminal_width",
    "get_render_width",
    "format_duration_seconds",
    "format_file_size",
    "truncate_text",
    "extract_last_qa_from_history",
    "MIN_RENDER_WIDTH",
    "MAX_RENDER_WIDTH",
    "WIDTH_MARGIN",
]