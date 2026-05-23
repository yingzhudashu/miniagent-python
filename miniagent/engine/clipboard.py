"""跨平台剪贴板辅助。

将文本写入系统剪贴板，在全屏 CLI 模式下作为 transcript 复制的备选方案。
支持 Windows（clip / ctypes）、macOS（pbcopy）、Linux（wl-copy / xclip）。
"""

from __future__ import annotations

import sys
import subprocess


def copy_text_to_system_clipboard(text: str) -> bool:
    """将纯文本写入系统剪贴板。

    全屏 CLI 无法用鼠标框选 transcript 时可用。返回 True 表示成功。

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
            except Exception:
                pass
            import ctypes

            GMEM_MOVEABLE = 0x0002
            CF_UNICODETEXT = 13
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            if not user32.OpenClipboard(0):
                return False
            try:
                if not user32.EmptyClipboard():
                    return False
                raw = te.encode("utf-16le") + b"\x00\x00"
                n = len(raw)
                h = kernel32.GlobalAlloc(GMEM_MOVEABLE, n)
                if not h:
                    return False
                p = kernel32.GlobalLock(h)
                if not p:
                    kernel32.GlobalFree(h)
                    return False
                try:
                    ctypes.memmove(p, raw, n)
                finally:
                    kernel32.GlobalUnlock(h)
                if not user32.SetClipboardData(CF_UNICODETEXT, h):
                    kernel32.GlobalFree(h)
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
            except Exception:
                continue
        return False
    except Exception:
        return False


__all__ = ["copy_text_to_system_clipboard"]
