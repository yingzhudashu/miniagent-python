"""miniagent/engine/clipboard.py 单元测试。"""

from __future__ import annotations

import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

from miniagent.engine.clipboard import copy_text_to_system_clipboard


def _run_ok(**kwargs) -> MagicMock:
    return MagicMock(returncode=0)


def _run_fail(**kwargs) -> MagicMock:
    return MagicMock(returncode=1)


def test_empty_text_returns_false() -> None:
    assert copy_text_to_system_clipboard("") is False


def test_normalizes_crlf_before_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    captured: list[bytes] = []

    def fake_run(argv, **kwargs):
        captured.append(kwargs["input"])
        return _run_ok()

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert copy_text_to_system_clipboard("a\r\nb") is True
    assert captured == [b"a\nb"]


def test_darwin_uses_pbcopy_utf8(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    calls: list[tuple[list[str], bytes]] = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs["input"]))
        return _run_ok()

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert copy_text_to_system_clipboard("hello") is True
    assert calls == [(["pbcopy"], b"hello")]


def test_darwin_pbcopy_failure_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(subprocess, "run", _run_fail)
    assert copy_text_to_system_clipboard("hello") is False


def test_linux_prefers_wl_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return _run_ok()

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert copy_text_to_system_clipboard("data") is True
    assert calls == [["wl-copy"]]


def test_linux_falls_back_to_xclip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if argv[0] == "wl-copy":
            return _run_fail()
        return _run_ok()

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert copy_text_to_system_clipboard("data") is True
    assert calls == [["wl-copy"], ["xclip", "-selection", "clipboard"]]


def test_linux_missing_tools_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")

    def fake_run(argv, **kwargs):
        raise FileNotFoundError(argv[0])

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert copy_text_to_system_clipboard("data") is False


def test_win32_uses_clip_utf16le(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    calls: list[tuple[list[str], bytes]] = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs["input"]))
        return _run_ok()

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert copy_text_to_system_clipboard("hi") is True
    assert calls == [(["clip"], "hi".encode("utf-16le"))]


def test_win32_falls_back_to_ctypes_when_clip_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "run", _run_fail)

    mock_user32 = MagicMock()
    mock_kernel32 = MagicMock()
    mock_user32.OpenClipboard.return_value = True
    mock_user32.EmptyClipboard.return_value = True
    mock_kernel32.GlobalAlloc.return_value = 1
    mock_kernel32.GlobalLock.return_value = 2
    mock_user32.SetClipboardData.return_value = True

    mock_windll = MagicMock()
    mock_windll.user32 = mock_user32
    mock_windll.kernel32 = mock_kernel32

    with patch("ctypes.windll", mock_windll), patch("ctypes.memmove"):
        assert copy_text_to_system_clipboard("fallback") is True

    mock_user32.OpenClipboard.assert_called_once()
    mock_user32.SetClipboardData.assert_called_once()


def test_win32_ctypes_open_clipboard_failure_returns_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "run", _run_fail)

    mock_user32 = MagicMock()
    mock_user32.OpenClipboard.return_value = False
    mock_windll = MagicMock()
    mock_windll.user32 = mock_user32

    with patch("ctypes.windll", mock_windll):
        assert copy_text_to_system_clipboard("x") is False


def test_outer_exception_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")

    def boom(argv, **kwargs):
        raise RuntimeError("subprocess broken")

    monkeypatch.setattr(subprocess, "run", boom)
    assert copy_text_to_system_clipboard("x") is False
