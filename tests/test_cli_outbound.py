"""CLI outbound contract mapping and delivery tests."""

from __future__ import annotations

import pytest

from miniagent.assistant.application.messaging import ChannelRegistry
from miniagent.assistant.contracts import ChannelAdapter, OutboundEventKind
from miniagent.assistant.engine.cli_outbound import (
    CliChannelAdapter,
    UnsupportedCliEventError,
    build_cli_outbound_event,
    build_cli_thinking_event,
)


def test_build_cli_outbound_event_retains_session_and_interface() -> None:
    """CLI output should retain routing without carrying UI callback objects."""
    event = build_cli_outbound_event("done", "session-1", interface="tui")

    assert event.kind is OutboundEventKind.FINAL
    assert event.target.channel == "cli"
    assert event.target.conversation_id == "session-1"
    assert event.content == "done"
    assert event.metadata == {"interface": "tui"}


def test_build_cli_outbound_event_rejects_empty_session() -> None:
    """An empty conversation target must fail before adapter dispatch."""
    with pytest.raises(ValueError, match="session_key"):
        build_cli_outbound_event("done", " ", interface="fallback")


@pytest.mark.asyncio
async def test_cli_adapter_routes_supported_kinds_without_changing_text() -> None:
    """The CLI adapter must select the registered renderer by normalized kind."""
    rendered: list[tuple[str, str, str]] = []
    adapter = CliChannelAdapter(
        lambda target, text: rendered.append(("final", target, text)),
        lambda target, text: rendered.append(("error", target, text)),
        lambda target, text: rendered.append(("status", target, text)),
        lambda event: rendered.append(
            ("thinking", event.target.conversation_id, event.content)
        ),
    )
    channels = ChannelRegistry([adapter])

    await channels.send(
        build_cli_outbound_event("**answer**", "session-2", interface="fallback")
    )
    await channels.send(
        build_cli_outbound_event(
            "failed\n",
            "session-2",
            interface="fallback",
            kind=OutboundEventKind.ERROR,
        )
    )
    await channels.send(
        build_cli_thinking_event(
            "delta",
            "session-2",
            interface="tui",
            fragment_kind="chunk",
        )
    )
    await channels.send(
        build_cli_outbound_event(
            "ready",
            "session-2",
            interface="fallback",
            kind=OutboundEventKind.STATUS,
        )
    )

    assert rendered == [
        ("final", "session-2", "**answer**"),
        ("error", "session-2", "failed\n"),
        ("thinking", "session-2", "delta"),
        ("status", "session-2", "ready"),
    ]
    assert isinstance(adapter, ChannelAdapter)


@pytest.mark.asyncio
async def test_cli_adapter_awaits_async_renderer() -> None:
    """Async renderers are awaited before delivery completes."""
    rendered: list[str] = []

    async def render(_target: str, text: str) -> None:
        rendered.append(text)

    adapter = CliChannelAdapter(render, render, render, lambda event: render("", event.content))
    await adapter.send(build_cli_outbound_event("done", "session-3", interface="tui"))
    assert rendered == ["done"]


@pytest.mark.asyncio
async def test_cli_adapter_rejects_unmigrated_event_kind() -> None:
    """Unmigrated kinds must fail explicitly instead of using the wrong format."""
    adapter = CliChannelAdapter(
        lambda _target, _text: None,
        lambda _target, _text: None,
        lambda _target, _text: None,
        lambda _event: None,
    )
    event = build_cli_outbound_event(
        "confirm", "session-4", interface="tui", kind=OutboundEventKind.CONFIRMATION
    )

    with pytest.raises(UnsupportedCliEventError, match="confirmation"):
        await adapter.send(event)


def test_build_cli_thinking_event_retains_sink_metadata() -> None:
    """Thinking fragments preserve label/chunk and optional ANSI rendering data."""
    event = build_cli_thinking_event(
        "fragment",
        "session-5",
        interface="fallback",
        fragment_kind="label",
        ansi_markdown="ansi-body",
    )

    assert event.kind is OutboundEventKind.THINKING_DELTA
    assert event.metadata == {
        "interface": "fallback",
        "fragment_kind": "label",
        "ansi_markdown": "ansi-body",
    }
