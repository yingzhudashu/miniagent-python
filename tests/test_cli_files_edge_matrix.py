"""CLI 文件标记的路径、类型、通知与降级契约。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from miniagent.engine import cli_files


def test_marker_path_description_type_and_replacement(tmp_path: Path) -> None:
    text = tmp_path / "note.txt"
    text.write_text("hello", encoding="utf-8")
    manager = SimpleNamespace(
        get=lambda _key: SimpleNamespace(workspace_path=str(tmp_path))
    )
    assert cli_files._resolve_marker_path(str(text), "s", manager) == str(text)
    assert cli_files._resolve_marker_path("note.txt", "s", manager) == str(text)
    assert cli_files._resolve_marker_path("missing", "s", None) == "missing"
    assert cli_files._read_file_description(str(text), "binary") == ""
    assert cli_files._read_file_description(str(text), "text") == "hello"
    assert cli_files._read_file_description(str(tmp_path / "missing"), "text") == ""
    assert cli_files._file_type_from_mime("image/png") == "image"
    assert cli_files._file_type_from_mime("text/plain") == "text"
    assert cli_files._file_type_from_mime("application/pdf") == "binary"
    assert cli_files._file_marker_replacement("a.bin", "binary", "") == "[文件: a.bin]"
    assert "图片内容" in cli_files._file_marker_replacement("a.png", "image", "x" * 200)
    assert "内容预览" in cli_files._file_marker_replacement("a.txt", "text", "text")


@pytest.mark.asyncio
async def test_inspect_and_describe_file_fallbacks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "image.png"
    path.write_bytes(b"header")
    monkeypatch.setattr(cli_files, "detect_mime_from_magic", lambda _data: "image/png")
    assert await cli_files._inspect_cli_file(str(path)) == ("image.png", "image/png", 6)

    monkeypatch.setattr(cli_files, "detect_mime_from_magic", MagicMock(side_effect=ValueError))
    assert (await cli_files._inspect_cli_file(str(path)))[1] == "application/octet-stream"

    runtime = SimpleNamespace(openai_client=object())
    monkeypatch.setattr(cli_files, "get_config", lambda *_args: True)
    monkeypatch.setattr(
        "miniagent.feishu.vision_desc.describe_image",
        AsyncMock(return_value="described"),
    )
    assert await cli_files._describe_cli_file(str(path), "image", runtime) == "described"

    monkeypatch.setattr(
        "miniagent.feishu.vision_desc.describe_image",
        AsyncMock(side_effect=RuntimeError("vision")),
    )
    assert await cli_files._describe_cli_file(str(path), "image", runtime) == ""
    assert await cli_files._describe_cli_file(str(path), "binary", runtime) == ""


def test_file_notifications_cover_size_and_summary() -> None:
    messages: list[str] = []

    def notify(text: str, _style: str) -> None:
        messages.append(text)

    cli_files._notify_processed_file(
        notify, file_name="large.txt", file_size=2048, description="x" * 101
    )
    cli_files._notify_processed_file(None, file_name="x", file_size=1, description="")
    assert "2KB" in messages[0]
    assert messages[1].rstrip().endswith("...")


@pytest.mark.asyncio
async def test_marker_memory_failure_and_outer_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "note.txt"
    path.write_text("hello", encoding="utf-8")
    notices: list[str] = []
    monkeypatch.setattr(cli_files, "_remember_cli_file", AsyncMock(side_effect=RuntimeError("db")))
    processed = await cli_files._process_cli_file_marker(
        file_path=str(path),
        session_key="s",
        session_manager=None,
        runtime_ctx=SimpleNamespace(),
        notify=lambda text, _style: notices.append(text),
    )
    assert processed is None and any("无法保存" in item for item in notices)

    monkeypatch.setattr(
        cli_files, "_process_cli_file_marker", AsyncMock(side_effect=RuntimeError("inspect"))
    )
    rewritten, files = await cli_files.process_cli_file_markers(
        f"read @file:{path}",
        "s",
        None,
        None,
        notify=lambda text, _style: notices.append(text),
    )
    assert rewritten.startswith("read @file:") and files == []
    assert any("处理文件失败" in item for item in notices)


@pytest.mark.asyncio
async def test_marker_no_match_is_identity() -> None:
    text, files = await cli_files.process_cli_file_markers("plain input", "s", None, None)
    assert text == "plain input" and files == []
