"""Mini Agent Python — 飞书适配类型 (Phase 8)

飞书消息事件、配置、响应相关类型。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FeishuConfig:
    """飞书应用配置

    Attributes:
        app_id: 飞书开放平台应用 App ID
        app_secret: 飞书开放平台应用 Secret
        port: HTTP 服务监听端口（Webhook 模式）
        encrypt_key: 事件加密密钥（可选）
        verification_token: 事件验证 Token（可选）
    """

    app_id: str
    app_secret: str
    port: int = 0
    encrypt_key: str | None = None
    verification_token: str | None = None


@dataclass
class FeishuMessageEvent:
    """飞书消息事件

    Attributes:
        message_id: 消息唯一 ID（用于去重）
        chat_id: 聊天 ID
        sender_id: 发送者 open_id
        msg_type: 消息类型（text/image/file 等）
        content: 消息内容
        timestamp: 消息时间戳
    """

    message_id: str
    chat_id: str
    sender_id: str
    msg_type: str
    content: str
    timestamp: str = ""


@dataclass
class FeishuReply:
    """飞书回复

    Attributes:
        content: 回复内容
        msg_type: 消息类型（默认 text）
        receive_id_type: 接收 ID 类型（chat_id/open_id）
    """

    content: str
    msg_type: str = "text"
    receive_id_type: str = "chat_id"


__all__ = ["FeishuConfig", "FeishuMessageEvent", "FeishuReply"]
