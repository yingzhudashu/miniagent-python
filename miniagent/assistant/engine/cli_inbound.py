"""Translate CLI text into platform-neutral inbound message contracts."""

from __future__ import annotations

from miniagent.ui.messages import InboundMessage

CLI_CHANNEL = "cli"
CLI_CONVERSATION_ID = "__cli__"
CLI_SENDER_ID = "local-user"


def build_cli_inbound_message(
    content: str,
    session_key: str,
    *,
    interface: str,
) -> InboundMessage:
    """Build a CLI message while retaining the resolved session identity."""
    return InboundMessage.create(
        channel=CLI_CHANNEL,
        conversation_id=CLI_CONVERSATION_ID,
        sender_id=CLI_SENDER_ID,
        content=content,
        session_key=session_key,
        metadata={"interface": interface},
    )


__all__ = [
    "CLI_CHANNEL",
    "CLI_CONVERSATION_ID",
    "CLI_SENDER_ID",
    "build_cli_inbound_message",
]
