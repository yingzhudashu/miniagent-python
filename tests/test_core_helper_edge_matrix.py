"""核心纯函数、状态迁移与受控 I/O 错误路径矩阵。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from miniagent.agent.execution_prompts import build_current_turn_user_context
from miniagent.agent.execution_stream import StreamingBuffer
from miniagent.agent.types.tool import ToolContext
from miniagent.assistant.engine.commands.help_commands import _md_escape_cell
from miniagent.assistant.engine.commands.instance_commands import handle_instance
from miniagent.assistant.engine.commands.runtime_commands import _capture_call, _respond
from miniagent.assistant.infrastructure import persistence
from miniagent.assistant.infrastructure.json_config import (
    _compatible_config_type,
    _validate_user_keys,
)
from miniagent.assistant.infrastructure.persistence import (
    StateMigrationError,
    StateSchema,
    load_state_file,
)
from miniagent.assistant.tools import exec as exec_module
from miniagent.assistant.tools import filesystem


def test_execution_prompt_and_stream_consolidation(tmp_path: Path) -> None:
    context = build_current_turn_user_context(
        user_input=" run ",
        plan_summary="plan",
        keyword_context="memory",
        kb_context="kb",
        session_files_root=str(tmp_path),
        risk_level="high",
        current_time_context="now",
        output_spec_block=" JSON ",
    )
    assert all(
        fragment in context
        for fragment in ("执行计划摘要", "JSON", "相关记忆", "相关知识库", "high", "now")
    )

    buffer = StreamingBuffer()
    for _ in range(51):
        buffer.append("x")
    assert buffer.getvalue() == "x" * 51
    buffer.append("tail")
    assert buffer.getvalue().endswith("tail")
    assert len(buffer) == 55
    buffer.clear()
    assert buffer.getvalue() == "" and len(buffer) == 0


def test_config_type_and_object_conflict_validation() -> None:
    assert _compatible_config_type(True, False)
    assert not _compatible_config_type(True, 1)
    assert _compatible_config_type(1.0, 1)
    assert not _compatible_config_type(1.0, True)
    assert _compatible_config_type(1, 2)
    assert not _compatible_config_type(1, False)
    assert _compatible_config_type("x", "y")
    with pytest.raises(ValueError, match="nested 应为 object"):
        _validate_user_keys({"nested": {"enabled": True}}, {"nested": "bad"})


def test_state_schema_rejects_invalid_migration_and_load_shape(tmp_path: Path) -> None:
    schema = StateSchema(name="bad", current_version=1, migrations={0: lambda _doc: []})  # type: ignore[dict-item]
    with pytest.raises(StateMigrationError, match="返回了非对象"):
        schema.migrate({})

    path = tmp_path / "state.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(StateMigrationError, match="顶层必须是 JSON 对象"):
        load_state_file("session_config", path)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(persistence, "migrate_state_file", lambda *_args, **_kwargs: None)
        with pytest.raises(StateMigrationError, match="顶层必须是 JSON 对象"):
            load_state_file("session_config", path)


@pytest.mark.asyncio
async def test_runtime_response_capture_and_instance_print(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    assert _respond("captured", capture=True) == "captured"
    assert _respond("printed", capture=False) is None
    assert "printed" in capsys.readouterr().out
    assert "命令执行失败" in _capture_call(lambda: 1 / 0)

    monkeypatch.setattr(
        "miniagent.assistant.engine.commands.instance_commands.cmd_instance_handler",
        lambda *_args, **_kwargs: print("instances"),
    )
    result = await handle_instance("/instance list", state={}, capture=False)
    assert result is None
    assert "instances" in capsys.readouterr().out
    assert _md_escape_cell(" a|b\r\nc ") == "a\\|b c"


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
