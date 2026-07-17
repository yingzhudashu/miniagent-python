"""Session isolation tests for ThinkingDisplay."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from miniagent.assistant.engine.thinking import ThinkingDisplay


def test_end_thinking_scoped_to_session() -> None:
    td = ThinkingDisplay()
    session_a = td._get_state("session_a")
    session_b = td._get_state("session_b")
    session_a.stream_step = 1
    session_a.stream_done = False
    session_b.stream_step = 2
    session_b.stream_done = False

    with patch.object(td, "_should_emit_cli", return_value=False):
        td.end_thinking("session_a")

    assert session_a.stream_done is True
    assert session_b.stream_done is False
    assert session_b.stream_step == 2


@pytest.mark.asyncio
async def test_sink_receives_session_key() -> None:
    display = ThinkingDisplay()
    received: list[tuple[str, str]] = []

    def sink(text: str, kind: str = "chunk", *, session_key: str = "") -> None:
        received.append((session_key, text))

    display.set_output_sink(sink)
    await display.show("hello", session_key="sk_a", streaming=False)

    assert received
    assert all(session_key == "sk_a" for session_key, _ in received)


@pytest.mark.asyncio
async def test_parallel_sessions_isolated_streaming() -> None:
    display = ThinkingDisplay()
    by_session: dict[str, list[str]] = {}

    def sink(text: str, kind: str = "chunk", *, session_key: str = "") -> None:
        by_session.setdefault(session_key, []).append(text)

    display.set_output_sink(sink)

    async def stream(session_key: str, first: str, second: str) -> None:
        await display.show(first, session_key=session_key, streaming=True, header="[plan]")
        await display.show(second, session_key=session_key, streaming=True, header="[plan]")

    await asyncio.gather(
        stream("A", "alpha", " more"),
        stream("B", "beta", " extra"),
    )

    output_a = "".join(by_session["A"])
    output_b = "".join(by_session["B"])
    assert "alpha" in output_a
    assert "beta" in output_b
    assert "alpha" not in output_b
    assert "beta" not in output_a
