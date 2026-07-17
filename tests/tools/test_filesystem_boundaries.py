"""Focused regressions migrated from test_recovery_edge_matrix.py."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from miniagent.agent.types.tool import ToolContext
from miniagent.assistant.tools import filesystem


@pytest.mark.asyncio
async def test_filesystem_move_copy_errors_and_recursive_depth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = ToolContext(cwd=str(tmp_path), allowed_paths=[str(tmp_path)])
    missing_move = await filesystem._move_file_handler(
        {"from": "missing", "to": "dest"}, ctx
    )
    missing_copy = await filesystem._copy_file_handler(
        {"from": "missing", "to": "dest"}, ctx
    )
    assert not missing_move.success and not missing_copy.success

    source = tmp_path / "source.txt"
    source.write_text("x", encoding="utf-8")
    monkeypatch.setattr(filesystem.shutil, "move", MagicMock(side_effect=OSError("move")))
    move_error = await filesystem._move_file_handler(
        {"from": "source.txt", "to": "moved.txt"}, ctx
    )
    monkeypatch.setattr(filesystem.shutil, "copy2", MagicMock(side_effect=OSError("copy")))
    copy_error = await filesystem._copy_file_handler(
        {"from": "source.txt", "to": "copy.txt"}, ctx
    )
    assert "移动失败" in move_error.content and "复制失败" in copy_error.content

    child = tmp_path / "child"
    child.mkdir()
    (child / "nested.txt").write_text("x", encoding="utf-8")
    entries, _ = filesystem._walk_directory(
        tmp_path, detail=True, max_depth=2, max_entries=20
    )
    assert any("nested.txt" in entry for entry in entries)
