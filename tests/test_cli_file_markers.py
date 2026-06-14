"""Tests for CLI ``@file:`` marker processing, bash helper, and history path."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from miniagent.engine.main import (
    _resolve_cli_history_file,
    detect_and_process_file_markers,
    run_cli_bash_command,
)


@pytest.mark.asyncio
async def test_detect_and_process_file_markers_text_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sample = tmp_path / "note.txt"
    sample.write_text("hello from file marker test", encoding="utf-8")

    session_manager = MagicMock()
    session_manager.get.return_value = MagicMock(workspace_path=str(tmp_path))

    messages: list[tuple[str, str]] = []

    def notify(msg: str, color: str) -> None:
        messages.append((msg, color))

    monkeypatch.setattr(
        "miniagent.memory.store.add_file_to_memory",
        AsyncMock(return_value=None),
    )

    processed, files_info = await detect_and_process_file_markers(
        f"please read @file:{sample.name}",
        "sess-1",
        session_manager,
        MagicMock(memory_store=None),
        notify=notify,
    )

    assert "@file:" not in processed
    assert "note.txt" in processed
    assert "please read" in processed
    assert len(files_info) == 1
    assert files_info[0]["name"] == "note.txt"
    assert any("已处理文件" in m[0] for m in messages)


@pytest.mark.asyncio
async def test_detect_and_process_file_markers_missing_file(tmp_path: Path) -> None:
    session_manager = MagicMock()
    session_manager.get.return_value = MagicMock(workspace_path=str(tmp_path))
    messages: list[str] = []

    processed, files_info = await detect_and_process_file_markers(
        "@file:missing.txt",
        "sess-1",
        session_manager,
        MagicMock(memory_store=None),
        notify=lambda msg, _color: messages.append(msg),
    )

    assert processed == "@file:missing.txt"
    assert files_info == []
    assert any("文件不存在" in m for m in messages)


def test_run_cli_bash_command_echo() -> None:
    ok, output = run_cli_bash_command("echo miniagent-bash-test")
    assert ok is True
    assert "miniagent-bash-test" in output


def test_run_cli_bash_command_nonzero_exit() -> None:
    ok, output = run_cli_bash_command("exit 42")
    assert ok is False
    assert "退出码: 42" in output


@pytest.mark.asyncio
async def test_detect_and_process_file_markers_file_prefix_without_at(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sample = tmp_path / "data.txt"
    sample.write_text("plain file marker", encoding="utf-8")

    session_manager = MagicMock()
    session_manager.get.return_value = MagicMock(workspace_path=str(tmp_path))

    monkeypatch.setattr(
        "miniagent.memory.store.add_file_to_memory",
        AsyncMock(return_value=None),
    )

    processed, files_info = await detect_and_process_file_markers(
        f"summarize file:{sample.name}",
        "sess-1",
        session_manager,
        MagicMock(memory_store=None),
    )

    assert "file:" not in processed
    assert "data.txt" in processed
    assert len(files_info) == 1


@pytest.mark.asyncio
async def test_detect_and_process_file_markers_multiple_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "a.txt"
    second = tmp_path / "b.txt"
    first.write_text("aaa", encoding="utf-8")
    second.write_text("bbb", encoding="utf-8")

    session_manager = MagicMock()
    session_manager.get.return_value = MagicMock(workspace_path=str(tmp_path))

    monkeypatch.setattr(
        "miniagent.memory.store.add_file_to_memory",
        AsyncMock(return_value=None),
    )

    processed, files_info = await detect_and_process_file_markers(
        f"@file:{first.name} and @file:{second.name}",
        "sess-1",
        session_manager,
        MagicMock(memory_store=None),
    )

    assert "@file:" not in processed
    assert "a.txt" in processed
    assert "b.txt" in processed
    assert len(files_info) == 2


def test_resolve_cli_history_file_under_state_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MINIAGENT_PATHS_STATE_DIR", str(tmp_path))
    path = _resolve_cli_history_file()
    assert path.endswith(os.path.join("cli", "history.txt"))
    assert os.path.isdir(os.path.dirname(path))


def test_cli_file_history_merge_is_memory_only(tmp_path: Path) -> None:
    from miniagent.engine.main import _create_cli_file_history

    history_path = tmp_path / "history.txt"
    history_path.write_text(
        "\n# ts\n+saved-command\n",
        encoding="utf-8",
    )
    hist = _create_cli_file_history(str(history_path))
    before = history_path.read_text(encoding="utf-8")

    hist.merge_strings_memory_only(["from-session", "saved-command"])

    assert history_path.read_text(encoding="utf-8") == before
    loaded = hist.get_strings()
    assert loaded[-1] == "from-session"
    assert any(s.strip() == "saved-command" for s in loaded)
    assert len(loaded) == 2


def test_reset_and_reload_transcript_does_not_reset_input_history() -> None:
    source = Path(__file__).resolve().parent.parent.joinpath(
        "miniagent", "engine", "main.py"
    ).read_text(encoding="utf-8")
    start = source.index("def _reset_and_reload_transcript(")
    end = source.index("def _trigger_lazy_load_more_history(", start)
    block = source[start:end]
    assert "input_buffer.history" not in block
    assert "_prime_cli_input_history_from_session" not in block


@pytest.mark.asyncio
async def test_fallback_cli_dispatches_help_via_dispatch_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fallback 与 TUI 一致：``/help`` 走 ``dispatch_command`` 而非 inline handler。"""
    from miniagent.engine.main import _run_cli_loop_fallback
    from miniagent.runtime.context import RuntimeContext

    dispatch_calls: list[str] = []

    async def fake_dispatch(user_input: str, **kwargs: object) -> str:
        dispatch_calls.append(user_input)
        return "HELP_OK"

    inputs = iter(["/help", "quit"])

    async def fake_to_thread(_fn, *_args, **_kwargs):
        return next(inputs)

    monkeypatch.setattr("asyncio.to_thread", fake_to_thread)
    monkeypatch.setattr(
        "miniagent.engine.command_dispatch.dispatch_command",
        fake_dispatch,
    )

    ctx = MagicMock(spec=RuntimeContext)
    ctx.engine = MagicMock()
    ctx.engine.thinking.set_output_sink = MagicMock()
    ctx.engine.get_confirmation_channel.return_value = None
    ctx.engine.set_active_session_key = MagicMock()
    ctx.registry = MagicMock()
    ctx.monitor = MagicMock()
    ctx.channel_router = MagicMock()
    ctx.message_queue = MagicMock()
    ctx.cli_transcript_coordinator = None
    ctx.cli_transcript_append = None
    ctx.clawhub = None
    ctx.memory_store = None
    ctx.activity_log = None
    ctx.keyword_index = None
    ctx.openai_client = None
    ctx.feishu = MagicMock()

    state = {
        "active_session_id": "default",
        "session_manager": None,
        "instance_id": 0,
        "feishu_p2p_synced_senders": set(),
    }

    monkeypatch.setattr(
        "miniagent.engine.main._print_history_summary_fallback",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr("miniagent.engine.session_continue.save_cli_session_state", lambda *_: None)
    monkeypatch.setattr("miniagent.engine.session_lock.release_session_lock", lambda *_: None)
    monkeypatch.setattr("miniagent.infrastructure.instance.unregister_instance", lambda: None)

    await _run_cli_loop_fallback(ctx, state, [], [])

    assert dispatch_calls == ["/help"]
