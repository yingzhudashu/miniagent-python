"""Direct contracts for split TUI appenders and one-turn orchestration."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from prompt_toolkit.formatted_text.ansi import ANSI

from miniagent.contracts.messages import InboundMessage
from miniagent.engine.cli_transcript import TranscriptBuffer
from miniagent.engine.cli_tui_appenders import create_transcript_appenders
from miniagent.engine.cli_tui_turn import create_tui_process_input


def test_transcript_appenders_merge_validate_scroll_and_plain(monkeypatch) -> None:
    transcript = TranscriptBuffer(1000)
    trims: list[bool] = []
    snaps: list[bool] = []
    invalidations: list[bool] = []
    monkeypatch.setattr(
        "miniagent.engine.cli_tui_appenders.get_app",
        lambda: SimpleNamespace(invalidate=lambda: invalidations.append(True)),
    )
    stick = [False]
    operations = create_transcript_appenders(
        is_valid_pt_style=lambda style: style != "bad",
        output_at_bottom=lambda: True,
        transcript=transcript,
        trim_transcript=lambda: trims.append(True),
        stick_bottom=stick,
        snap_output_bottom=lambda: snaps.append(True),
        safe_ansi=lambda value: value,
    )
    operations.append_transcript("bad", "one")
    operations.append_transcript("", "two")
    operations.append_transcript("", "")
    ansi = ANSI("ansi")
    operations.append_transcript("x", ansi=ansi)
    operations.append_ansi_transcript(ANSI("more"))

    assert transcript[0] == ("", "onetwo")
    assert operations.transcript_plain().endswith("ansimore")
    assert len(trims) == 4 and snaps and invalidations
    assert stick[0] is True


def test_transcript_appenders_preserve_scroll_when_not_at_bottom(monkeypatch) -> None:
    transcript = TranscriptBuffer(1000)
    monkeypatch.setattr(
        "miniagent.engine.cli_tui_appenders.get_app",
        MagicMock(side_effect=RuntimeError("no app")),
    )
    stick = [False]
    operations = create_transcript_appenders(
        is_valid_pt_style=lambda _style: True,
        output_at_bottom=lambda: False,
        transcript=transcript,
        trim_transcript=lambda: None,
        stick_bottom=stick,
        snap_output_bottom=MagicMock(),
        safe_ansi=lambda value: value,
    )
    operations.append_transcript("", "text")
    operations.append_ansi_transcript(SimpleNamespace(value="ansi"))
    assert stick[0] is False


@pytest.mark.asyncio
async def test_tui_turn_success_and_error_paths(monkeypatch) -> None:
    coordinator = SimpleNamespace(
        begin_turn=MagicMock(),
        make_session_append=lambda _key: MagicMock(),
        end_turn=MagicMock(),
    )
    drains: list[str] = []
    dispatcher = SimpleNamespace(
        drain=AsyncMock(side_effect=lambda target: drains.append(target.conversation_id))
    )
    sent: list[object] = []
    channels = SimpleNamespace(send=AsyncMock(side_effect=sent.append))

    @asynccontextmanager
    async def session_turn(_key):
        yield

    engine = SimpleNamespace(
        session_turn=session_turn,
        run_agent_with_thinking=AsyncMock(return_value=" reply "),
    )
    runtime = SimpleNamespace(
        clawhub=None,
        memory=None,
        knowledge_registry=None,
        openai_client=None,
    )
    state = {"active_session_id": "s", "session_manager": None}
    monkeypatch.setattr(
        "miniagent.engine.cli_tui_turn.process_cli_file_markers",
        AsyncMock(return_value=("normalized", [])),
    )
    monkeypatch.setattr(
        "miniagent.engine.cli_format.format_cli_user_block", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        "miniagent.engine.cli_tui_turn.get_app",
        lambda: SimpleNamespace(invalidate=lambda: None),
    )
    def event_builder(text, session_key, **kwargs):
        return SimpleNamespace(content=text, session_key=session_key, kwargs=kwargs)
    process = create_tui_process_input(
        channel_router=SimpleNamespace(),
        state=state,
        runtime_context=runtime,
        term_write=MagicMock(),
        transcript_coordinator=coordinator,
        engine=engine,
        cli_rule_heavy=MagicMock(),
        output_at_bottom=lambda: True,
        stick_bottom=[False],
        snap_output_bottom=MagicMock(),
        rule_line_width=lambda: 80,
        skill_toolboxes=lambda: [],
        skill_prompts=lambda: [],
        registry=None,
        monitor=None,
        cli_outbound_dispatcher=dispatcher,
        outbound_channels=channels,
        build_cli_outbound_event=event_builder,
        outbound_event_kind=SimpleNamespace(ERROR="error"),
    )
    message = InboundMessage(
        event_id="event",
        channel="cli",
        conversation_id="cli",
        sender_id="user",
        content="input",
        received_at=datetime.now(timezone.utc),
        session_key="session",
    )
    await process(message)
    assert drains == ["session", "session"]
    assert sent[-1].content == "reply"
    coordinator.end_turn.assert_called_once_with("session")

    engine.run_agent_with_thinking.side_effect = RuntimeError("failed")
    await process(message)
    assert "failed" in sent[-1].content
    assert sent[-1].kwargs["kind"] == "error"
