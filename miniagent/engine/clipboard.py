"""跨平台剪贴板辅助。

将文本写入系统剪贴板，在全屏 CLI 模式下作为 transcript 复制的备选方案。

平台与依赖：
- Windows: ``clip``（内置）或 ctypes Win32 API 回退
- macOS: ``pbcopy``（系统自带）
- Linux: ``wl-copy``（Wayland）或 ``xclip``（X11），需自行安装其一
"""

from __future__ import annotations

import logging
import subprocess
import sys

_logger = logging.getLogger(__name__)


def _run_clipboard_command(argv: list[str], payload: bytes) -> bool:
    """调用一个剪贴板命令并把缺失程序、超时和非零退出统一降级。"""
    try:
        result = subprocess.run(
            argv,
            input=payload,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except Exception as error:
        _logger.debug("%s 调用失败: %s", argv[0], error, exc_info=True)
        return False
    if result.returncode != 0:
        _logger.debug("%s 退出码非零: %s", argv[0], result.returncode)
        return False
    return True


def _copy_windows_api(text: str) -> bool:
    """使用 Win32 全局内存把 Unicode 文本所有权转交给系统剪贴板。"""
    import ctypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    if not user32.OpenClipboard(0):
        return False
    handle = None
    try:
        if not user32.EmptyClipboard():
            return False
        raw = text.encode("utf-16le") + b"\x00\x00"
        handle = kernel32.GlobalAlloc(0x0002, len(raw))
        if not handle:
            return False
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            kernel32.GlobalFree(handle)
            return False
        try:
            ctypes.memmove(pointer, raw, len(raw))
        finally:
            kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(13, handle):
            kernel32.GlobalFree(handle)
            return False
        handle = None
        return True
    finally:
        user32.CloseClipboard()


def _copy_linux(text: str) -> bool:
    """按 Wayland、X11 顺序尝试 Linux 剪贴板命令。"""
    payload = text.encode("utf-8")
    return any(
        _run_clipboard_command(argv, payload)
        for argv in (["wl-copy"], ["xclip", "-selection", "clipboard"])
    )


def copy_text_to_system_clipboard(text: str) -> bool:
    """将纯文本写入系统剪贴板。

    全屏 CLI 无法用鼠标框选 transcript 时可用。

    Args:
        text: 待复制的纯文本。空字符串直接返回 ``False``，不写入剪贴板。

    Returns:
        写入成功为 ``True``；空文本、缺少工具或写入失败为 ``False``。
        失败时不抛出异常，仅记录 debug 日志。

    平台优先级：
    - Windows: 先尝试 ``clip`` 命令，失败后回退 ctypes Win32 API
    - macOS: ``pbcopy``
    - Linux: ``wl-copy``（Wayland）→ ``xclip``（X11）
    """
    if not text:
        return False
    normalized = text.replace("\r\n", "\n")
    try:
        if sys.platform == "win32":
            return _run_clipboard_command(
                ["clip"], normalized.encode("utf-16le")
            ) or _copy_windows_api(normalized)
        if sys.platform == "darwin":
            return _run_clipboard_command(["pbcopy"], normalized.encode("utf-8"))
        return _copy_linux(normalized)
    except (AttributeError, OSError, TypeError) as error:
        _logger.debug("剪贴板写入异常: %s", error, exc_info=True)
        return False


__all__ = ["copy_text_to_system_clipboard"]
