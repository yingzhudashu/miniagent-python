"""Feishu channel configuration and normalized text ingress data.

通道类型只从本模块导入，网络与 SDK 细节留在 transport 模块。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FeishuConfig:
    """飞书应用配置（WebSocket 长连接模式）。

    Attributes:
        app_id: 飞书开放平台应用 App ID
        app_secret: 飞书开放平台应用 Secret
        encrypt_key: 事件加密密钥（WS 事件分发，可选）
        verification_token: 事件验证 Token（WS 事件分发，可选）
    """

    app_id: str
    app_secret: str
    encrypt_key: str | None = None
    verification_token: str | None = None


@dataclass
class FeishuInboundText:
    """飞书入站文本（及线程元数据），供 ``poll_server`` → Agent handler 使用。"""

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
