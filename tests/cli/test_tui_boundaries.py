"""Focused regressions migrated from test_type_boundary_regressions.py."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from miniagent.assistant.infrastructure.cli_transcript_coordinator import CliTranscriptCoordinator
from miniagent.ui.tui.transcript import TranscriptBuffer


def test_transcript_buffer_supports_index_reads() -> None:
    buffer = TranscriptBuffer(100, min_fragments=0)
    buffer.append(("class:test", "value"))
    assert buffer[0] == ("class:test", "value")

def test_buffered_ansi_fragment_flushes_after_live_turn() -> None:
    ansi_objects: list[object] = []
    coordinator = CliTranscriptCoordinator(
        lambda _style, _text: None,
        ansi_objects.append,
        parallel_sessions=True,
    )
    coordinator.begin_turn("live")
    coordinator.begin_turn("buffered")
    marker = object()
    coordinator.append_ansi("buffered", marker)
    coordinator.end_turn("buffered")
    assert ansi_objects == []
    coordinator.end_turn("live")
    assert ansi_objects == [marker]

@pytest.mark.asyncio
async def test_tui_force_fallback_after_optional_imports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import miniagent.assistant.engine.cli_tui as tui

    fallback = AsyncMock()
    monkeypatch.setattr(tui, "run_cli_loop_fallback", fallback)
    monkeypatch.setattr(tui.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(tui.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(tui, "get_config", lambda key, default=None: key == "cli.force_fallback")
    ctx = SimpleNamespace(
        engine=None,
        registry=None,
        monitor=None,
        channel_router=None,
        message_queue=None,
        outbound_channels=SimpleNamespace(),
        cli_outbound_dispatcher=None,
    )
    await tui.run_cli_loop(ctx, {}, [], [])
    fallback.assert_awaited_once()
