"""CLI 终端辅助工具函数。

提供统一的终端宽度计算、状态格式化、文件类型检测等公共工具，
避免在 CLI surfaces、feishu_handler.py 和其他模块中重复实现。

模块职责：
- 终端宽度计算（自适应宽屏）
- 状态信息格式化
- 历史记录提取辅助
- 文件 magic bytes 检测（MIME/扩展名）
- 飞书状态行输出

非职责：
- 不处理用户输入（属于 cli_tui.py / cli_fallback.py）
- 不处理 thinking 输出（属于 thinking.py）
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from typing import Any

from miniagent.bootstrap.application import ApplicationContainer
from miniagent.core.constants import RENDER_MAX_WIDTH, RENDER_MIN_WIDTH, RENDER_WIDTH_MARGIN

# ─── 终端宽度计算 ───────────────────────────────────────────────

MIN_RENDER_WIDTH = RENDER_MIN_WIDTH
MAX_RENDER_WIDTH = RENDER_MAX_WIDTH
WIDTH_MARGIN = RENDER_WIDTH_MARGIN


def get_terminal_width(fallback_width: int = 80) -> int:
    """获取终端列宽（自适应宽屏显示器）。

    Args:
        fallback_width: ``shutil.get_terminal_size`` 失败时的默认宽度

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
    计算公式：``max(40, min(500, terminal_width - 4))``

    Args:
        fallback_width: 终端宽度检测失败时传给 ``get_terminal_width`` 的回退值

    Returns:
        渲染宽度（最小 40，最大 500）
    """
    terminal_width = get_terminal_width(fallback_width)
    return max(MIN_RENDER_WIDTH, min(MAX_RENDER_WIDTH, terminal_width - WIDTH_MARGIN))


# ─── 状态格式化辅助 ───────────────────────────────────────────────

def format_duration_seconds(seconds: float) -> str:
    """格式化秒数为人类可读形式。

    Args:
        seconds: 非负秒数（负值按 0 处理）

    Returns:
        格式化字符串，如 ``"45.2s"`` 或 ``"2m30s"``
    """
    seconds = max(0.0, seconds)
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
        size_bytes: 非负字节数（负值按 0 处理）

    Returns:
        格式化字符串，如 ``"150KB"`` 或 ``"2.5MB"``
    """
    size_bytes = max(0, size_bytes)
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
    """截断文本到指定长度（含后缀）。

    Args:
        text: 原始文本
        max_length: 结果字符串的最大长度（须 ``>= 0``）
        suffix: 截断后缀；当 ``max_length`` 小于后缀长度时，仅保留后缀的前
            ``max_length`` 个字符

    Returns:
        截断后的文本，长度不超过 ``max_length``
    """
    if max_length <= 0:
        return ""
    if len(text) <= max_length:
        return text
    if max_length <= len(suffix):
        return suffix[:max_length]
    return text[: max_length - len(suffix)] + suffix


# ─── 会话历史辅助 ───────────────────────────────────────────────

def _message_content_to_str(content: Any) -> str:
    """将消息 ``content`` 归一化为纯文本（支持多模态 list 结构）。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") if item.get("text") is not None else item.get("content")
                if text is not None:
                    parts.append(str(text))
        return "\n".join(parts)
    return str(content)


def extract_last_qa_from_history(history: list[dict[str, Any]]) -> tuple[str, str] | None:
    """从历史记录中提取最后一轮问答。

    配对规则：取**最后一条** ``assistant`` 消息，以及其时间线上**之前最近的一条**
    ``user`` 消息；中间的 ``system`` / ``tool`` 等角色会被跳过。
    ``content`` 可为字符串或多模态 list（提取其中的 ``text`` / ``content`` 字段）。

    Args:
        history: OpenAI 风格历史消息列表（``role`` + ``content``）

    Returns:
        ``(用户问题, Agent 回复)`` 元组；缺任一角色或归一化后为空则 ``None``
    """
    if not history:
        return None

    user_msg: str | None = None
    assistant_msg: str | None = None

    for msg in reversed(history):
        role = msg.get("role", "")
        if role == "assistant" and assistant_msg is None:
            assistant_msg = _message_content_to_str(msg.get("content", ""))
        elif role == "user" and user_msg is None:
            user_msg = _message_content_to_str(msg.get("content", ""))
            break

    if user_msg and assistant_msg:
        return (user_msg, assistant_msg)
    return None


# ─── 文件 magic bytes 检测 ───────────────────────────────────────────────

# 轻量级前缀表；复杂格式（WebP）在 detect_ext_from_magic 中单独校验。
# 局限：ZIP 含 Office 文档、MKV 与 WebM 共用 magic、BMP 仅两字节前缀。
_MAGIC_TABLE: list[tuple[bytes, str]] = [
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
    (b"BM", ".bmp"),
    (b"\x00\x00\x01\x00", ".ico"),
    (b"\x1a\x45\xdf\xa3", ".webm"),  # WebM/MKV
    (b"\x00\x00\x00\x1cftyp", ".mp4"),  # MP4 (ftyp 后通常为 isom/avc1 等)
    (b"\x00\x00\x00\x20ftyp", ".mp4"),
    (b"PK\x03\x04", ".zip"),  # ZIP / DOCX / XLSX / PPTX 等 Office 格式
    (b"%PDF", ".pdf"),
]


def detect_ext_from_magic(data: bytes) -> str | None:
    """根据文件头 magic bytes 检测扩展名。

    按前缀匹配，无需完整文件头；数据越短，可识别类型越少。
    WebP 需 ``RIFF....WEBP`` 完整标记，避免将 WAV/AVI 等 RIFF 容器误判。

    Args:
        data: 文件二进制数据（任意长度，通常读取文件前几字节即可）

    Returns:
        检测到的扩展名（如 ``".png"``），或 ``None``
    """
    if not data:
        return None
    for magic, ext in _MAGIC_TABLE:
        if data[: len(magic)] == magic:
            return ext
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def detect_mime_from_magic(data: bytes) -> str | None:
    """根据文件头 magic bytes 检测 MIME 类型。

    基于 ``detect_ext_from_magic``；Office 文档（docx/xlsx 等）会映射为
    ``application/zip``，未知扩展名回退 ``application/octet-stream``。

    Args:
        data: 文件二进制数据

    Returns:
        MIME 类型字符串，或 ``None``（无法识别时）
    """
    ext = detect_ext_from_magic(data)
    if not ext:
        return None
    mime_map: dict[str, str] = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
        ".webp": "image/webp",
        ".ico": "image/x-icon",
        ".webm": "video/webm",
        ".mp4": "video/mp4",
        ".zip": "application/zip",
        ".pdf": "application/pdf",
    }
    return mime_map.get(ext, "application/octet-stream")


# ─── 飞书状态行输出 ───────────────────────────────────────────────

def feishu_user_status_fn(ctx: ApplicationContainer) -> Callable[[str], None]:
    """飞书状态行输出函数工厂。

    全屏 CLI 已注册 ``cli_transcript_append`` 时写入 transcript，否则 ``print``。
    ``cli_transcript_append`` 抛异常时同样退回 ``print``。

    Args:
        ctx: 运行时上下文

    Returns:
        状态行输出函数 ``(msg: str) -> None``
    """

    def _emit(msg: str) -> None:
        """将飞书状态行写入全屏 transcript（样式 ``cli-muted``）或退回 ``print``。"""
        fn = ctx.cli_transcript_append
        line = msg if msg.endswith("\n") else msg + "\n"
        if fn is not None:
            try:
                fn("class:cli-muted", line)
            except Exception:
                print(msg, flush=True)
        else:
            print(msg, flush=True)

    return _emit


__all__ = [
    "get_terminal_width",
    "get_render_width",
    "format_duration_seconds",
    "format_file_size",
    "truncate_text",
    "extract_last_qa_from_history",
    "detect_ext_from_magic",
    "detect_mime_from_magic",
    "feishu_user_status_fn",
    "MIN_RENDER_WIDTH",
    "MAX_RENDER_WIDTH",
    "WIDTH_MARGIN",
]
