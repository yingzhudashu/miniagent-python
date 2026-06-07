"""Tests for miniagent.tools.filesystem — core file operation tools."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from miniagent.tools.filesystem import (
    _copy_file_handler,
    _create_dir_handler,
    _delete_file_handler,
    _edit_file_handler,
    _list_dir_handler,
    _move_file_handler,
    _read_file_handler,
    _write_file_handler,
)
from miniagent.types.tool import ToolContext


@pytest.fixture
def ctx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ToolContext:
    """ToolContext sandboxed to tmp_path; also chdir so relative paths resolve correctly."""
    monkeypatch.chdir(tmp_path)
    return ToolContext(cwd=str(tmp_path), allowed_paths=[str(tmp_path)])


# ─── read_file ───


async def test_read_file_basic(tmp_path: Path, ctx: ToolContext) -> None:
    f = tmp_path / "test.txt"
    f.write_text("line1\nline2\nline3\n", encoding="utf-8")
    r = await _read_file_handler({"path": "test.txt"}, ctx)
    assert r.success
    assert "line1" in r.content
    assert r.meta["totalLines"] == 4
    assert "rag_ingested" in r.meta
    assert r.meta["source_path"] == str(f.resolve())


async def test_read_file_pagination(tmp_path: Path, ctx: ToolContext) -> None:
    f = tmp_path / "big.txt"
    lines = "\n".join(f"line{i}" for i in range(1, 21))
    f.write_text(lines + "\n", encoding="utf-8")
    r = await _read_file_handler({"path": "big.txt", "offset": 1, "limit": 5}, ctx)
    assert r.success
    assert r.meta["totalLines"] == 21
    assert r.meta["readLines"] == 5
    assert "仅显示 5 行" in r.content


async def test_read_file_rag_ingest_can_be_disabled(tmp_path: Path, ctx: ToolContext) -> None:
    f = tmp_path / "disabled.txt"
    f.write_text("content", encoding="utf-8")

    def _get_config(key: str, default=None):
        if key == "knowledge.auto_ingest_analyzed_files":
            return False
        return default

    with patch("miniagent.knowledge.file_ingest.get_config", side_effect=_get_config):
        r = await _read_file_handler({"path": "disabled.txt"}, ctx)

    assert r.success
    assert r.meta["rag_ingested"] is False
    assert r.meta["rag_ingest_skipped"] is True
    assert r.meta["rag_ingest_reason"] == "disabled"


# ─── write_file ───


async def test_write_file_creates(ctx: ToolContext, tmp_path: Path) -> None:
    r = await _write_file_handler({"path": "sub/new.txt", "content": "hello"}, ctx)
    assert r.success
    assert (tmp_path / "sub" / "new.txt").read_text(encoding="utf-8") == "hello"


async def test_write_file_overwrites(ctx: ToolContext, tmp_path: Path) -> None:
    f = tmp_path / "existing.txt"
    f.write_text("old", encoding="utf-8")
    r = await _write_file_handler({"path": "existing.txt", "content": "new"}, ctx)
    assert r.success
    assert f.read_text(encoding="utf-8") == "new"


# ─── edit_file ───


async def test_edit_file_unique_match(ctx: ToolContext, tmp_path: Path) -> None:
    f = tmp_path / "edit.txt"
    f.write_text("hello world", encoding="utf-8")
    r = await _edit_file_handler({"path": "edit.txt", "oldText": "world", "newText": "there"}, ctx)
    assert r.success
    assert f.read_text(encoding="utf-8") == "hello there"


async def test_edit_file_no_match(ctx: ToolContext, tmp_path: Path) -> None:
    f = tmp_path / "edit2.txt"
    f.write_text("hello", encoding="utf-8")
    r = await _edit_file_handler({"path": "edit2.txt", "oldText": "missing", "newText": "x"}, ctx)
    assert not r.success
    assert "未找到" in r.content


async def test_edit_file_multiple_matches(ctx: ToolContext, tmp_path: Path) -> None:
    f = tmp_path / "edit3.txt"
    f.write_text("foo foo foo", encoding="utf-8")
    r = await _edit_file_handler({"path": "edit3.txt", "oldText": "foo", "newText": "bar"}, ctx)
    assert not r.success
    assert "3 处匹配" in r.content


# ─── list_dir ───


async def test_list_dir_basic(ctx: ToolContext, tmp_path: Path) -> None:
    (tmp_path / "a.txt").touch()
    (tmp_path / "sub").mkdir()
    r = await _list_dir_handler({"path": "."}, ctx)
    assert r.success
    assert "a.txt" in r.content
    assert "sub" in r.content


async def test_list_dir_recursive(ctx: ToolContext, tmp_path: Path) -> None:
    (tmp_path / "a.txt").touch()
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.txt").touch()
    r = await _list_dir_handler({"path": ".", "recursive": True}, ctx)
    assert r.success
    assert "a.txt" in r.content
    assert "b.txt" in r.content


async def test_list_dir_not_a_dir(ctx: ToolContext, tmp_path: Path) -> None:
    f = tmp_path / "file.txt"
    f.touch()
    r = await _list_dir_handler({"path": str(f)}, ctx)
    assert not r.success
    assert "目录不存在" in r.content


# ─── create_dir ───


async def test_create_dir(ctx: ToolContext, tmp_path: Path) -> None:
    r = await _create_dir_handler({"path": "a/b/c"}, ctx)
    assert r.success
    assert (tmp_path / "a" / "b" / "c").is_dir()


async def test_create_dir_idempotent(ctx: ToolContext, tmp_path: Path) -> None:
    (tmp_path / "x").mkdir()
    r = await _create_dir_handler({"path": "x"}, ctx)
    assert r.success


# ─── move_file ───


async def test_move_file(ctx: ToolContext, tmp_path: Path) -> None:
    f = tmp_path / "src.txt"
    f.write_text("data", encoding="utf-8")
    r = await _move_file_handler({"from": "src.txt", "to": "dst.txt"}, ctx)
    assert r.success
    assert not f.exists()
    assert (tmp_path / "dst.txt").read_text(encoding="utf-8") == "data"


# ─── copy_file ───


async def test_copy_file(ctx: ToolContext, tmp_path: Path) -> None:
    f = tmp_path / "orig.txt"
    f.write_text("data", encoding="utf-8")
    r = await _copy_file_handler({"from": "orig.txt", "to": "copy.txt"}, ctx)
    assert r.success
    assert f.exists()  # original preserved
    assert (tmp_path / "copy.txt").read_text(encoding="utf-8") == "data"


# ─── delete_file ───


async def test_delete_file(ctx: ToolContext, tmp_path: Path) -> None:
    f = tmp_path / "del.txt"
    f.write_text("gone", encoding="utf-8")
    r = await _delete_file_handler({"path": "del.txt"}, ctx)
    assert r.success
    assert not f.exists()


async def test_delete_dir_requires_recursive(ctx: ToolContext, tmp_path: Path) -> None:
    d = tmp_path / "deldir"
    d.mkdir()
    r = await _delete_file_handler({"path": "deldir"}, ctx)
    assert not r.success
    assert "recursive=true" in r.content.lower()


async def test_delete_dir_recursive(ctx: ToolContext, tmp_path: Path) -> None:
    d = tmp_path / "deldir"
    d.mkdir()
    (d / "inner.txt").touch()
    r = await _delete_file_handler({"path": "deldir", "recursive": True}, ctx)
    assert r.success
    assert not d.exists()
