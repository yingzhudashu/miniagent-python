"""Mini Agent Python — 飞书集成类型

飞书消息事件、卡片交互、配置相关类型。
用于飞书开放平台事件推送的解析与响应。

支持的场景：
- 接收飞书消息事件（im.message.receive_v1）
- 发送文本/卡片回复
- Webhook URL 验证
- 飞书应用配置管理
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FeishuMessageEvent:
    """飞书消息事件

    表示从飞书开放平台接收到的消息或卡片交互事件。

    Attributes:
        type: 事件类型：message=文本消息，card_action=卡片按钮点击
        chat_id: 飞书聊天 ID（群聊或个人会话）
        sender_id: 发送者 ID（open_id）
        content: 消息内容（文本或卡片 payload）
        message_id: 消息唯一 ID（用于去重）
        card_callback: 卡片回调数据（仅 card_action 类型有效）
    """

    type: str  # "message" | "card_action"
    chat_id: str
    sender_id: str
    content: str
    message_id: str | None = None
    card_callback: dict[str, Any] | None = None
    # card_callback 结构: {"action": str, "value": dict[str, str]}


@dataclass
class FeishuConfig:
    """飞书应用配置

    用于初始化飞书 SDK 客户端的配置参数。

    Attributes:
        app_id: 飞书开放平台应用 App ID
        app_secret: 飞书开放平台应用 App Secret
        port: Webhook HTTP 服务器监听端口（长轮询模式可设为 0）
        enable_encrypt: 是否启用事件加密
        encrypt_key: 事件加密密钥
        verification_token: 事件验证令牌
    """

    app_id: str
    app_secret: str
    port: int = 0
    enable_encrypt: bool = False
    encrypt_key: str | None = None
    verification_token: str | None = None


@dataclass
class FeishuMessagePayload:
    """飞书发送消息的请求负载

    Attributes:
        msg_type: 消息类型：text=文本，interactive=卡片，image=图片，file=文件
        receive_id: 接收者 ID（chat_id、open_id 或 email）
        content: 消息内容，JSON 字符串（如 '{"text":"hello"}'）
    """

    msg_type: str  # "text" | "interactive" | "image" | "file"
    receive_id: str
    content: str


@dataclass
class AgentMessageResult:
    """Agent 处理后的消息结果

    Attributes:
        text: 回复文本内容
        use_card: 是否以卡片格式回复
        card_elements: 卡片模板元素
    """

    text: str
    use_card: bool = False
    card_elements: list[dict[str, Any]] = field(default_factory=list)


__all__ = [
    "FeishuMessageEvent",
    "FeishuConfig",
    "FeishuMessagePayload",
    "AgentMessageResult",
]
