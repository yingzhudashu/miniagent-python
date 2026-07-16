"""Workspace lifecycle tests."""

from __future__ import annotations

import pytest

from miniagent.assistant.session.workspace import WorkspaceManager


@pytest.mark.asyncio
async def test_copy_tree_async_preserves_nested_files(tmp_path) -> None:
    source = tmp_path / "source"
    nested = source / "subdir"
    nested.mkdir(parents=True)
    (source / "file.txt").write_text("test content", encoding="utf-8")
    (nested / "nested.txt").write_text("nested content", encoding="utf-8")
    destination = tmp_path / "destination"
    destination.mkdir()

    manager = WorkspaceManager(base_dir=str(tmp_path))
    await manager._copy_tree_async(str(source), str(destination))

    assert (destination / "file.txt").read_text(encoding="utf-8") == "test content"
    assert (destination / "subdir" / "nested.txt").read_text(encoding="utf-8") == (
        "nested content"
    )


@pytest.mark.asyncio
async def test_create_workspace_async_creates_files_directory(tmp_path) -> None:
    manager = WorkspaceManager(base_dir=str(tmp_path / "sessions"))

    result = await manager.create_workspace_async(session_id="test-session", parent_path=None)

    assert result["workspace_path"]
    assert result["files_path"]
    assert (tmp_path / "sessions" / "test-session" / "files").is_dir()


@pytest.mark.asyncio
async def test_destroy_workspace_async_removes_workspace(tmp_path) -> None:
    manager = WorkspaceManager(base_dir=str(tmp_path / "sessions"))
    result = await manager.create_workspace_async(session_id="test-destroy")

    assert await manager.destroy_workspace_async("test-destroy") is True
    assert not (tmp_path / "sessions" / "test-destroy").exists()
    assert result["workspace_path"]
