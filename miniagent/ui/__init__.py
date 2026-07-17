"""Reusable CLI, TUI and channel contracts built on public Agent events."""

from miniagent.ui.channels import ChannelAdapter, ChannelRegistry, ChannelRegistryProtocol
from miniagent.ui.contracts import UIInput, UIInputKind, UISurface, UITarget
from miniagent.ui.messages import (
    Attachment,
    ChannelTarget,
    InboundMessage,
    OutboundEvent,
    OutboundEventKind,
)
from miniagent.ui.runtime import TuiActions, TuiApp, TuiEvent, TuiSnapshot, TuiUpdate
from miniagent.ui.surfaces import CLISurface, FeishuSurface, QueueUISurface, TUISurface

__all__ = [
    "Attachment",
    "ChannelAdapter",
    "ChannelRegistry",
    "ChannelRegistryProtocol",
    "ChannelTarget",
    "CLISurface",
    "FeishuSurface",
    "InboundMessage",
    "OutboundEvent",
    "OutboundEventKind",
    "TuiActions",
    "TuiApp",
    "TuiEvent",
    "TuiSnapshot",
    "TuiUpdate",
    "TUISurface",
    "UIInput",
    "UIInputKind",
    "UISurface",
    "UITarget",
    "QueueUISurface",
]
