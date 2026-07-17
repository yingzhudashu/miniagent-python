"""Fallback CLI 的脚本化命令、Agent turn 与清理流程测试。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from miniagent.assistant.engine.cli_fallback import run_cli_loop_fallback
from miniagent.ui.channels import ChannelRegistry


class _Queue:
    async def dispatch(self, _key, awaitable, *_callbacks) -> None:
        await awaitable

    async def dispatch_wait(self, _key, awaitable, *_callbacks) -> None:
        await awaitable


class _Engine:
    def __init__(self) -> None:
        self.thinking = SimpleNamespace(set_output_sink=lambda sink: setattr(self, "sink", sink))
        self.inputs: list[tuple[str, str]] = []

    @asynccontextmanager
    async def session_turn(self, _session_key):
        yield

    async def run_agent_with_thinking(self, user_input, session_key, *_args, **_kwargs):
        self.inputs.append((user_input, session_key))
        return "agent reply"

    def get_confirmation_channel(self, _key):
        return None

    def set_active_session_key(self, key):
        self.active = key


def _context() -> SimpleNamespace:
    return SimpleNamespace(
        engine=_Engine(),
        registry=MagicMock(),
        monitor=MagicMock(),
        channel_router=SimpleNamespace(
            CLI_CHANNEL="cli",
            resolve=lambda _channel: "default",
        ),
        message_queue=_Queue(),
        outbound_channels=ChannelRegistry(),
        cli_outbound_dispatcher=None,
        cli_transcript_coordinator=None,
        cli_transcript_append=None,
        clawhub=None,
        memory=None,
        knowledge_registry=None,
        llm_gateway=None,
        register_shutdown_tracked_task=lambda _task: None,
    )


def _patch_lifecycle(monkeypatch, inputs: list[str]) -> None:
    iterator = iter(inputs)

    async def fake_to_thread(_fn, *_args, **_kwargs):
        return next(iterator)

    monkeypatch.setattr("asyncio.to_thread", fake_to_thread)
    monkeypatch.setattr("miniagent.assistant.engine.cli_fallback.resolve_cli_history_file", lambda: "missing")
    monkeypatch.setattr("miniagent.assistant.engine.cli_fallback.prime_fallback_readline_history", lambda *_: None)
    monkeypatch.setattr("miniagent.assistant.engine.cli_fallback.heartbeat", lambda: None)
    monkeypatch.setattr("miniagent.assistant.engine.cli_fallback.unregister_instance", lambda: None)
    monkeypatch.setattr("miniagent.assistant.engine.session_continue.save_cli_session_state", lambda *_: None)
    monkeypatch.setattr("miniagent.assistant.engine.session_lock.release_session_lock", lambda *_: None)
    monkeypatch.setattr(
        "miniagent.assistant.engine.cli_fallback.print_history_summary_fallback", lambda *_args, **_kwargs: None
    )


@pytest.mark.asyncio
async def test_fallback_shell_command_status_and_agent_turn(monkeypatch, capsys) -> None:
    ctx = _context()
    _patch_lifecycle(monkeypatch, ["", "!echo hello", "/status", "ask", "quit"])
    monkeypatch.setattr(
        "miniagent.assistant.engine.cli_fallback.run_cli_shell_command", lambda _cmd: (0, "shell output")
    )

    async def dispatch(_text, **_kwargs):
        return "status output"

    monkeypatch.setattr("miniagent.assistant.engine.command_dispatch.dispatch_command", dispatch)
    state = {"active_session_id": "default", "session_manager": None, "instance_id": 1}
    await run_cli_loop_fallback(ctx, state, [], [])

    output = capsys.readouterr().out
    assert "shell output" in output and "status output" in output
    assert ctx.engine.inputs == [("ask", "default")]
    assert ctx.cli_transcript_append is None


@pytest.mark.asyncio
@pytest.mark.parametrize(("plain", "copied", "expected"), [("history", True, "已复制"), ("history", False, "复制失败"), ("", False, "无历史")])
async def test_fallback_copy_outcomes(monkeypatch, capsys, plain, copied, expected) -> None:
    ctx = _context()
    _patch_lifecycle(monkeypatch, ["/copy", "quit"])
    monkeypatch.setattr("miniagent.assistant.engine.commands.session_management.build_session_history_plaintext", lambda *_: plain)
    monkeypatch.setattr("miniagent.assistant.engine.cli_fallback.copy_text_to_system_clipboard", lambda _text: copied)
    state = {"active_session_id": "default", "session_manager": None, "instance_id": 1}
    await run_cli_loop_fallback(ctx, state, [], [])
    assert expected in capsys.readouterr().out
