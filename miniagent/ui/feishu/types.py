"""Feishu transport configuration and normalized inbound text."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FeishuConfig:
    """Credentials and optional verification material for a Feishu app."""

    app_id: str
    app_secret: str
    encrypt_key: str | None = None
    verification_token: str | None = None


@dataclass
class FeishuInboundText:
    """SDK-neutral subset of a Feishu text or interactive message event."""

    text: str
    chat_id: str
    sender_id: str
    chat_type: str
    message_id: str = ""
    root_id: str | None = None
    parent_id: str | None = None
    thread_id: str | None = None
    create_time: int = 0


__all__ = ["FeishuConfig", "FeishuInboundText"]
