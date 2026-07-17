"""Translate CLI replies into platform-neutral outbound event contracts."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from miniagent.assistant.engine.cli_inbound import CLI_CHANNEL
from miniagent.ui.messages import ChannelTarget, OutboundEvent, OutboundEventKind

CliEventRenderer = Callable[[str, str], Awaitable[None] | None]
CliThinkingRenderer = Callable[[OutboundEvent], Awaitable[None] | None]


class UnsupportedCliEventError(ValueError):
    """The CLI adapter has no behavior-preserving renderer for an event kind."""


@dataclass(frozen=True, slots=True)
class CliChannelAdapter:
    """Route normalized CLI events back through the existing UI renderers."""

    final_renderer: CliEventRenderer
    error_renderer: CliEventRenderer
    status_renderer: CliEventRenderer
    thinking_renderer: CliThinkingRenderer
    channel_id: str = field(default=CLI_CHANNEL, init=False)

    async def send(self, event: OutboundEvent) -> None:
        """Render supported event kinds without changing their original format."""
        if event.kind is OutboundEventKind.THINKING_DELTA:
            result = self.thinking_renderer(event)
        elif event.kind is OutboundEventKind.FINAL:
            renderer = self.final_renderer
            result = renderer(event.target.conversation_id, event.content)
        elif event.kind is OutboundEventKind.ERROR:
            renderer = self.error_renderer
            result = renderer(event.target.conversation_id, event.content)
        elif event.kind is OutboundEventKind.STATUS:
            renderer = self.status_renderer
            result = renderer(event.target.conversation_id, event.content)
        else:
            raise UnsupportedCliEventError(
                f"CLI event kind {event.kind.value!r} has no registered renderer"
            )
        if inspect.isawaitable(result):
            await result


def build_cli_outbound_event(
    content: str,
    session_key: str,
    *,
    interface: str,
    kind: OutboundEventKind = OutboundEventKind.FINAL,
    metadata: Mapping[str, Any] | None = None,
) -> OutboundEvent:
    """Build a CLI event while retaining the target session and interface."""
    session_key = session_key.strip()
    if not session_key:
        raise ValueError("CLI outbound session_key must not be empty")
    event_metadata = dict(metadata or {})
    event_metadata["interface"] = interface
    return OutboundEvent.create(
        kind=kind,
        target=ChannelTarget(channel=CLI_CHANNEL, conversation_id=session_key),
        content=content,
        metadata=event_metadata,
    )


def build_cli_thinking_event(
    fragment: str,
    session_key: str,
    *,
    interface: str,
    fragment_kind: str,
    ansi_markdown: str | None = None,
) -> OutboundEvent:
    """Build one ordered thinking fragment without interpreting its UI payload."""
    return build_cli_outbound_event(
        fragment,
        session_key,
        interface=interface,
        kind=OutboundEventKind.THINKING_DELTA,
        metadata={
            "fragment_kind": fragment_kind,
            "ansi_markdown": ansi_markdown,
        },
    )


__all__ = [
    "CliChannelAdapter",
    "UnsupportedCliEventError",
    "build_cli_outbound_event",
    "build_cli_thinking_event",
]
