"""Tests for miniagent.tools.session_memory — diary read/search tools."""

from __future__ import annotations

from pathlib import Path

import pytest

from miniagent.tools.session_memory import (
    _diary_query_positions,
    _read_session_diary_handler,
    _search_session_diary_handler,
    session_memory_tools,
)
from miniagent.types.tool import ToolContext
from miniagent.utils.session_id import safe_session_id


@pytest.fixture
def memory_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """将状态根目录指向临时目录。"""
    from miniagent.infrastructure import paths

    root = str(tmp_path)
    monkeypatch.setattr(paths, "resolve_state_dir", lambda: root)
    monkeypatch.setattr("miniagent.tools.session_memory.resolve_state_dir", lambda: root)
    monkeypatch.setattr("miniagent.memory.history_archive.get_state_root", lambda: root)
    return tmp_path


def _write_diary(memory_root: Path, session_key: str, day: str, content: str) -> Path:
    diary_dir = memory_root / "memory" / "diary" / safe_session_id(session_key)
    diary_dir.mkdir(parents=True, exist_ok=True)
    fp = diary_dir / f"{day}.md"
    fp.write_text(content, encoding="utf-8")
    return fp


@pytest.mark.asyncio
async def test_read_session_diary_success(memory_root: Path) -> None:
    sk = "sess-read"
    day = "2026-06-15"
    _write_diary(memory_root, sk, day, "hello diary")
    ctx = ToolContext(cwd=str(memory_root), session_key=sk, allowed_paths=[str(memory_root)])
    r = await _read_session_diary_handler({"day": day}, ctx)
    assert r.success
    assert "hello diary" in r.content
    assert r.meta and "path" in r.meta


@pytest.mark.asyncio
async def test_read_session_diary_no_session_key(memory_root: Path) -> None:
    ctx = ToolContext(cwd=str(memory_root), allowed_paths=[str(memory_root)])
    r = await _read_session_diary_handler({}, ctx)
    assert not r.success
    assert "session_key" in r.content


@pytest.mark.asyncio
async def test_read_session_diary_missing_file(memory_root: Path) -> None:
    ctx = ToolContext(cwd=str(memory_root), session_key="sess-miss", allowed_paths=[str(memory_root)])
    r = await _read_session_diary_handler({"day": "1999-01-01"}, ctx)
    assert not r.success
    assert "未找到" in r.content


@pytest.mark.asyncio
async def test_search_session_diary_multiple_hits(memory_root: Path) -> None:
    sk = "sess-search"
    _write_diary(memory_root, sk, "2026-06-14", "alpha needle beta needle gamma")
    ctx = ToolContext(cwd=str(memory_root), session_key=sk, allowed_paths=[str(memory_root)])
    r = await _search_session_diary_handler({"query": "needle"}, ctx)
    assert r.success
    assert "2026-06-14.md (#1)" in r.content
    assert "2026-06-14.md (#2)" in r.content


@pytest.mark.asyncio
async def test_search_session_diary_respects_max_hits_per_file(memory_root: Path) -> None:
    sk = "sess-cap"
    _write_diary(memory_root, sk, "2026-06-14", "x x x x x")
    ctx = ToolContext(cwd=str(memory_root), session_key=sk, allowed_paths=[str(memory_root)])
    r = await _search_session_diary_handler(
        {"query": "x", "max_hits_per_file": 2},
        ctx,
    )
    assert r.success
    assert "(#1)" in r.content
    assert "(#2)" in r.content
    assert "(#3)" not in r.content


@pytest.mark.asyncio
async def test_search_session_diary_empty_dir(memory_root: Path) -> None:
    ctx = ToolContext(cwd=str(memory_root), session_key="sess-empty", allowed_paths=[str(memory_root)])
    r = await _search_session_diary_handler({"query": "nope"}, ctx)
    assert r.success
    assert "尚无 diary" in r.content


def test_diary_query_positions() -> None:
    assert _diary_query_positions("aaa", "a", 10) == [0, 1, 2]
    assert _diary_query_positions("abc", "z", 5) == []


def test_session_memory_tools_exported() -> None:
    assert "read_session_diary" in session_memory_tools
    assert "search_session_diary" in session_memory_tools
