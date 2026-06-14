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
    te = text.replace("\r\n", "\n")
    try:
        if sys.platform == "win32":
            try:
                r = subprocess.run(
                    ["clip"],
                    input=te.encode("utf-16le"),
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
                if r.returncode == 0:
                    return True
                _logger.debug("clip命令退出码非零: %s", r.returncode)
            except Exception as e:
                _logger.debug("clip命令失败: %s", e)
            import ctypes

            GMEM_MOVEABLE = 0x0002
            CF_UNICODETEXT = 13
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            if not user32.OpenClipboard(0):
                _logger.debug("Win32 OpenClipboard 失败")
                return False
            try:
                if not user32.EmptyClipboard():
                    _logger.debug("Win32 EmptyClipboard 失败")
                    return False
                raw = te.encode("utf-16le") + b"\x00\x00"
                n = len(raw)
                h = kernel32.GlobalAlloc(GMEM_MOVEABLE, n)
                if not h:
                    _logger.debug("Win32 GlobalAlloc 失败")
                    return False
                p = kernel32.GlobalLock(h)
                if not p:
                    kernel32.GlobalFree(h)
                    _logger.debug("Win32 GlobalLock 失败")
                    return False
                try:
                    ctypes.memmove(p, raw, n)
                finally:
                    kernel32.GlobalUnlock(h)
                if not user32.SetClipboardData(CF_UNICODETEXT, h):
                    kernel32.GlobalFree(h)
                    _logger.debug("Win32 SetClipboardData 失败")
                    return False
                return True
            finally:
                user32.CloseClipboard()
        if sys.platform == "darwin":
            r = subprocess.run(
                ["pbcopy"],
                input=te.encode("utf-8"),
                capture_output=True,
                timeout=10,
                check=False,
            )
            if r.returncode != 0:
                _logger.debug("pbcopy退出码非零: %s", r.returncode)
            return r.returncode == 0
        for argv in (["wl-copy"], ["xclip", "-selection", "clipboard"]):
            try:
                r = subprocess.run(
                    argv,
                    input=te.encode("utf-8"),
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
                if r.returncode == 0:
                    return True
                _logger.debug("%s 退出码非零: %s", argv[0], r.returncode)
            except Exception as e:
                _logger.debug("%s 调用失败: %s", argv[0], e)
                continue
        _logger.debug("Linux 剪贴板工具均不可用或失败（需 wl-copy 或 xclip）")
        return False
    except Exception as e:
        _logger.debug("剪贴板写入异常: %s", e)
        return False


__all__ = ["copy_text_to_system_clipboard"]
