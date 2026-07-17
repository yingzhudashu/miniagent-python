"""Focused regressions migrated from test_core_helper_edge_matrix.py."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from miniagent.agent.types.tool import ToolContext
from miniagent.assistant.tools import exec as exec_module
from miniagent.assistant.tools import filesystem


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "fragment"),
    [
        (PermissionError(), "权限不足"),
        (IsADirectoryError(), "路径是目录"),
        (UnicodeDecodeError("utf-8", b"x", 0, 1, "bad"), "UTF-8"),
    ],
)
async def test_read_file_maps_specific_io_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    fragment: str,
) -> None:
    monkeypatch.setattr(filesystem, "_read_file_page_sync", MagicMock(side_effect=error))
    result = await filesystem._read_file_handler(
        {"path": "file.txt"},
        ToolContext(cwd=str(tmp_path), allowed_paths=[str(tmp_path)]),
    )
    assert not result.success and fragment in result.content

@pytest.mark.asyncio
async def test_write_file_permission_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(filesystem, "atomic_write_text", MagicMock(side_effect=PermissionError))
    result = await filesystem._write_file_handler(
        {"path": "file.txt", "content": "x"},
        ToolContext(cwd=str(tmp_path), allowed_paths=[str(tmp_path)]),
    )
    assert not result.success and "权限不足" in result.content

def test_directory_helpers_handle_stat_and_permission_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "item.txt"
    path.write_text("x", encoding="utf-8")
    monkeypatch.setattr(Path, "stat", MagicMock(side_effect=OSError))
    assert filesystem._format_directory_entry(path, detail=True).endswith("item.txt")

    monkeypatch.setattr(
        filesystem, "_limited_sorted_items", MagicMock(side_effect=PermissionError)
    )
    entries, truncated = filesystem._walk_directory(
        tmp_path, detail=False, max_depth=1, max_entries=1
    )
    assert entries == [] and not truncated

@pytest.mark.asyncio
async def test_exec_format_termination_cancel_and_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    formatted = exec_module._format_exec_result(
        b"out", b"err", truncated=True, returncode=2
    )
    assert not formatted.success and "已截断" in formatted.content and "stderr" in formatted.content

    exited = SimpleNamespace(returncode=0, kill=MagicMock(), wait=AsyncMock())
    await exec_module._terminate_exec_process(exited)
    exited.kill.assert_not_called()

    gone = SimpleNamespace(
        returncode=None,
        kill=MagicMock(side_effect=ProcessLookupError),
        wait=AsyncMock(),
    )
    await exec_module._terminate_exec_process(gone)

    proc = SimpleNamespace(returncode=None)
    monkeypatch.setattr(exec_module, "create_tracked_subprocess", AsyncMock(return_value=proc))
    terminate = AsyncMock()
    monkeypatch.setattr(exec_module, "_terminate_exec_process", terminate)
    monkeypatch.setattr(exec_module, "deregister_process", AsyncMock())
    monkeypatch.setattr(exec_module, "_communicate_limited", AsyncMock(side_effect=RuntimeError("io")))
    result = await exec_module._run_exec_process("echo x", ".", 1)
    assert not result.success and "io" in result.content
    terminate.assert_awaited_once_with(proc)

    monkeypatch.setattr(
        exec_module, "_communicate_limited", AsyncMock(side_effect=asyncio.CancelledError)
    )
    with pytest.raises(asyncio.CancelledError):
        await exec_module._run_exec_process("echo x", ".", 1)
