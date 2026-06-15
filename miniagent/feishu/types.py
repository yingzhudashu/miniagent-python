"""飞书侧数据类型（配置、入站消息事件、回复载体）

供 ``poll_server``、``agent_handler`` 与 ``miniagent.types`` 再导出使用；
网络与 SDK 细节仍在各调用模块内处理，本文件保持无 I/O 纯数据。

亦通过 ``miniagent.types`` 再导出，便于 ``from miniagent.types import FeishuConfig``。

**运行时入站类型**：请使用 :class:`FeishuInboundText`。
:class:`FeishuMessageEvent` / :class:`FeishuReply` 为历史兼容别名，新代码勿依赖。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FeishuConfig:
    """飞书应用配置（WebSocket 长连接模式）。

    Attributes:
        app_id: 飞书开放平台应用 App ID
        app_secret: 飞书开放平台应用 Secret
        port: 预留字段；当前实现仅 WebSocket 入站，不使用 HTTP 监听端口
        encrypt_key: 事件加密密钥（WS 事件分发，可选）
        verification_token: 事件验证 Token（WS 事件分发，可选）
    """

    app_id: str
    app_secret: str
    port: int = 0
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


@dataclass
class FeishuMessageEvent:
    """飞书消息事件（legacy：保留类型再导出；运行时使用 :class:`FeishuInboundText`）。

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
    """飞书回复（legacy：保留类型再导出；出站由 ``poll_server._send_reply`` / ``im_send`` 处理）。

    Attributes:
        content: 回复内容
        msg_type: 消息类型（默认 text）
        receive_id_type: 接收 ID 类型（chat_id/open_id）
    """

    content: str
    msg_type: str = "text"
    receive_id_type: str = "chat_id"


__all__ = ["FeishuConfig", "FeishuInboundText", "FeishuMessageEvent", "FeishuReply"]
