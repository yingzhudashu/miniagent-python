"""Fallback CLI 运行时对象的分派与降级合同。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from miniagent.engine import cli_fallback


def _runtime() -> cli_fallback._FallbackCliRuntime:
    runtime = object.__new__(cli_fallback._FallbackCliRuntime)
    runtime.state = {"active_session_id": "s1", "session_manager": object()}
    runtime.ctx = SimpleNamespace(
        background_tasks=None,
        clawhub=None,
        memory=None,
        knowledge_registry=None,
        openai_client=None,
    )
    runtime.engine = SimpleNamespace(
        set_active_session_key=MagicMock(),
        get_confirmation_channel=MagicMock(return_value=None),
    )
    runtime.registry = object()
    runtime.monitor = object()
    runtime.channel_router = object()
    runtime.outbound_channels = SimpleNamespace(send=AsyncMock())
    runtime.inbound_turns = SimpleNamespace(submit=AsyncMock())
    runtime.process_input = AsyncMock()
    runtime.history_file = "history.txt"
    runtime.readline_module = None
    runtime.print_locked = MagicMock()
    runtime.show_history = MagicMock()
    runtime.skill_toolboxes = lambda: []
    runtime.skill_prompts = lambda: []
    return runtime


@pytest.mark.asyncio
async def test_handle_line_shell_copy_stop_and_exit(monkeypatch) -> None:
    runtime = _runtime()
    assert await runtime._handle_line("exit") is True
    monkeypatch.setattr(cli_fallback, "run_cli_shell_command", lambda _cmd: (True, "done"))
    assert await runtime._handle_line("!echo ok") is False
    runtime.print_locked.assert_called_with("done")

    runtime._copy_history = MagicMock()
    await runtime._handle_line("/copy")
    runtime._copy_history.assert_called_once()

    shutdown = AsyncMock()
    monkeypatch.setattr(cli_fallback, "shutdown_runtime", shutdown)
    assert await runtime._handle_line("/stop") is True
    shutdown.assert_awaited_once()


@pytest.mark.asyncio
async def test_command_switch_status_and_exit(monkeypatch) -> None:
    runtime = _runtime()
    import miniagent.engine.command_dispatch as dispatch_module
    import miniagent.engine.parallel_config as parallel_module

    async def switch(*_args, **_kwargs):
        runtime.state["active_session_id"] = "s2"
        return "ok"

    monkeypatch.setattr(dispatch_module, "dispatch_command", switch)
    monkeypatch.setattr(parallel_module, "resolve_active_session_key", lambda *_args: "s2")
    assert await runtime._handle_command("/session s2") is False
    runtime.show_history.assert_called_once()
    runtime.outbound_channels.send.assert_awaited_once()

    monkeypatch.setattr(dispatch_module, "dispatch_command", AsyncMock(return_value="__EXIT__"))
    assert await runtime._handle_command("/exit") is True


@pytest.mark.asyncio
async def test_submit_clarification_and_maintenance(monkeypatch) -> None:
    runtime = _runtime()
    import miniagent.engine.parallel_config as parallel_module
    from miniagent.types.confirmation import ConfirmationStage

    monkeypatch.setattr(parallel_module, "resolve_active_session_key", lambda *_args: "s1")
    channel = SimpleNamespace(
        has_pending=True,
        pending=SimpleNamespace(stage=ConfirmationStage.CLARIFICATION),
        respond=MagicMock(),
    )
    runtime.engine.get_confirmation_channel.return_value = channel
    await runtime._submit_agent("answer")
    channel.respond.assert_called_once()

    runtime.engine.get_confirmation_channel.return_value = None
    runtime._maintain_runtime_files = MagicMock()
    await runtime._submit_agent("question")
    runtime.inbound_turns.submit.assert_awaited_once()
    runtime._maintain_runtime_files.assert_called_once()

    history = SimpleNamespace(write_history_file=MagicMock())
    runtime.readline_module = history
    runtime._maintain_runtime_files = cli_fallback._FallbackCliRuntime._maintain_runtime_files.__get__(runtime)
    monkeypatch.setattr(cli_fallback, "heartbeat", lambda: None)
    runtime._maintain_runtime_files()
    history.write_history_file.assert_called_once_with("history.txt")


def test_copy_history_outcomes(monkeypatch, capsys) -> None:
    runtime = _runtime()
    import miniagent.engine.cli_commands as commands

    monkeypatch.setattr(commands, "build_session_history_plaintext", lambda *_args: "plain")
    monkeypatch.setattr(cli_fallback, "copy_text_to_system_clipboard", lambda _text: True)
    runtime._copy_history()
    assert "已复制" in capsys.readouterr().out

    monkeypatch.setattr(commands, "build_session_history_plaintext", lambda *_args: "")
    runtime._copy_history()
    assert "无历史" in capsys.readouterr().out


def test_thinking_delivery_and_cleanup(monkeypatch) -> None:
    runtime = _runtime()
    runtime.coordinator = SimpleNamespace(
        is_live=MagicMock(return_value=True), defer=MagicMock()
    )
    runtime.thinking_sink("chunk", session_key="s")
    runtime.print_locked.assert_called_with("chunk", end="")
    runtime.coordinator.is_live.return_value = False
    runtime.thinking_sink("later", session_key="s")
    runtime.coordinator.defer.assert_called_once()

    runtime.dispatcher = SimpleNamespace(publish=MagicMock(return_value="task"))
    runtime.ctx.register_shutdown_tracked_task = MagicMock()
    runtime._publish_thinking("fragment", session_key="s")
    runtime.ctx.register_shutdown_tracked_task.assert_called_with("task")

    runtime.engine.thinking = SimpleNamespace(set_output_sink=MagicMock())
    import miniagent.engine.session_continue as continue_module
    import miniagent.engine.session_lock as lock_module

    monkeypatch.setattr(continue_module, "save_cli_session_state", MagicMock())
    monkeypatch.setattr(lock_module, "release_session_lock", MagicMock())
    monkeypatch.setattr(cli_fallback, "unregister_instance", lambda: None)
    runtime._cleanup()
    runtime.engine.thinking.set_output_sink.assert_called_with(None)


def test_history_summary_user_assistant_and_failures(monkeypatch, capsys) -> None:
    manager = SimpleNamespace(
        load_session_history_range=lambda *_args, **_kwargs: (
            [
                {"role": "user", "content": "question"},
                {"role": "assistant", "content": "answer"},
                {"role": "thinking", "content": "ignored"},
            ],
            5,
        )
    )
    import miniagent.engine.markdown_cli as markdown_module

    monkeypatch.setattr(markdown_module, "cli_raw_markdown_enabled", lambda: True)
    cli_fallback.print_history_summary_fallback(
        manager,
        "s",
        rule_heavy=lambda: print("heavy"),
        rule_light=lambda: print("light"),
        get_width=lambda: 80,
        header="history",
    )
    output = capsys.readouterr().out
    assert "question" in output and "answer" in output and "还有 2" in output

    broken = SimpleNamespace(
        load_session_history_range=lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("bad"))
    )
    cli_fallback.print_history_summary_fallback(
        broken,
        "s",
        rule_heavy=lambda: None,
        rule_light=lambda: None,
        get_width=lambda: 80,
    )
