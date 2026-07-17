"""Tests for CLI ``@file:`` marker processing, bash helper, and history path."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from miniagent.assistant.engine.cli_files import process_cli_file_markers
from miniagent.assistant.engine.cli_history import (
    create_cli_file_history,
    resolve_cli_history_file,
)
from miniagent.assistant.engine.cli_shell import run_cli_shell_command
from miniagent.ui.channels import ChannelRegistry
from tests.memory_helpers import make_memory_runtime


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
        "miniagent.assistant.memory.store.add_file_to_memory",
        AsyncMock(return_value=None),
    )

    processed, files_info = await process_cli_file_markers(
        f"please read @file:{sample.name}",
        "sess-1",
        session_manager,
        MagicMock(memory=make_memory_runtime()),
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

    processed, files_info = await process_cli_file_markers(
        "@file:missing.txt",
        "sess-1",
        session_manager,
        MagicMock(memory=make_memory_runtime()),
        notify=lambda msg, _color: messages.append(msg),
    )

    assert processed == "@file:missing.txt"
    assert files_info == []
    assert any("文件不存在" in m for m in messages)


def test_run_cli_bash_command_echo() -> None:
    ok, output = run_cli_shell_command("echo miniagent-bash-test")
    assert ok is True
    assert "miniagent-bash-test" in output


def test_run_cli_bash_command_nonzero_exit() -> None:
    ok, output = run_cli_shell_command("exit 42")
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
        "miniagent.assistant.memory.store.add_file_to_memory",
        AsyncMock(return_value=None),
    )

    processed, files_info = await process_cli_file_markers(
        f"summarize file:{sample.name}",
        "sess-1",
        session_manager,
        MagicMock(memory=make_memory_runtime()),
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
        "miniagent.assistant.memory.store.add_file_to_memory",
        AsyncMock(return_value=None),
    )

    processed, files_info = await process_cli_file_markers(
        f"@file:{first.name} and @file:{second.name}",
        "sess-1",
        session_manager,
        MagicMock(memory=make_memory_runtime()),
    )

    assert "@file:" not in processed
    assert "a.txt" in processed
    assert "b.txt" in processed
    assert len(files_info) == 2


def test_resolve_cli_history_file_under_state_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MINIAGENT_PATHS_STATE_DIR", str(tmp_path))
    path = resolve_cli_history_file()
    assert path.endswith(os.path.join("cli", "history.txt"))
    assert os.path.isdir(os.path.dirname(path))


def test_cli_file_history_merge_is_memory_only(tmp_path: Path) -> None:
    history_path = tmp_path / "history.txt"
    history_path.write_text(
        "\n# ts\n+saved-command\n",
        encoding="utf-8",
    )
    hist = create_cli_file_history(str(history_path))
    before = history_path.read_text(encoding="utf-8")

    hist.merge_strings_memory_only(["from-session", "saved-command"])

    assert history_path.read_text(encoding="utf-8") == before
    loaded = hist.get_strings()
    assert loaded[-1] == "from-session"
    assert any(s.strip() == "saved-command" for s in loaded)
    assert len(loaded) == 2


def test_reset_and_reload_transcript_does_not_reset_input_history() -> None:
    source = Path(__file__).resolve().parent.parent.joinpath(
        "miniagent", "assistant", "engine", "cli_tui_transcript_ops.py"
    ).read_text(encoding="utf-8")
    start = source.index("def reset_and_reload_transcript(")
    end = source.index("def trigger_lazy_load_more_history(", start)
    block = source[start:end]
    assert "input_buffer.history" not in block
    assert "_prime_cli_input_history_from_session" not in block


@pytest.mark.asyncio
async def test_fallback_cli_dispatches_help_via_dispatch_command(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Fallback 与 TUI 一致：``/help`` 走 ``dispatch_command`` 而非 inline handler。"""
    from miniagent.assistant.bootstrap.application import ApplicationContainer
    from miniagent.assistant.engine.cli_fallback import run_cli_loop_fallback

    dispatch_calls: list[str] = []

    async def fake_dispatch(user_input: str, **kwargs: object) -> str:
        dispatch_calls.append(user_input)
        return "HELP_OK"

    inputs = iter(["/help", "quit"])

    async def fake_to_thread(_fn, *_args, **_kwargs):
        return next(inputs)

    monkeypatch.setattr("asyncio.to_thread", fake_to_thread)
    monkeypatch.setattr(
        "miniagent.assistant.engine.command_dispatch.dispatch_command",
        fake_dispatch,
    )

    ctx = MagicMock(spec=ApplicationContainer)
    ctx.engine = MagicMock()
    ctx.engine.thinking.set_output_sink = MagicMock()
    ctx.engine.get_confirmation_channel.return_value = None
    ctx.engine.set_active_session_key = MagicMock()
    ctx.registry = MagicMock()
    ctx.monitor = MagicMock()
    ctx.channel_router = MagicMock()
    ctx.message_queue = MagicMock()
    ctx.outbound_channels = ChannelRegistry()
    ctx.cli_transcript_coordinator = None
    ctx.cli_transcript_append = None
    ctx.clawhub = None
    ctx.memory = make_memory_runtime()
    ctx.llm_gateway = None
    ctx.feishu = MagicMock()

    state = {
        "active_session_id": "default",
        "session_manager": None,
        "instance_id": 0,
        "feishu_p2p_synced_senders": set(),
    }

    monkeypatch.setattr(
        "miniagent.assistant.engine.cli_fallback.print_history_summary_fallback",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr("miniagent.assistant.engine.session_continue.save_cli_session_state", lambda *_: None)
    monkeypatch.setattr("miniagent.assistant.engine.session_lock.release_session_lock", lambda *_: None)
    monkeypatch.setattr("miniagent.assistant.infrastructure.instance.unregister_instance", lambda: None)

    await run_cli_loop_fallback(ctx, state, [], [])

    assert dispatch_calls == ["/help"]
    assert "HELP_OK" in capsys.readouterr().out
