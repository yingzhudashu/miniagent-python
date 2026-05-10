"""Mini Agent Python — Feishu 消息处理器

处理飞书消息事件，将消息转发给 Agent 处理。

导出：
- create_feishu_handler(): 创建飞书消息处理器
"""

from __future__ import annotations

from typing import Awaitable, Callable

from miniagent.feishu.types import FeishuMessageEvent, FeishuReply
from miniagent.infrastructure.logger import get_logger

_logger = get_logger(__name__)


# 消息处理回调类型
MessageHandler = Callable[[FeishuMessageEvent], Awaitable[FeishuReply]]


def create_feishu_handler(
    *,
    on_message: MessageHandler | None = None,
    auto_reply: bool = True,
    mention_required: bool = True,
) -> MessageHandler:
    """创建飞书消息处理器。

    Args:
        on_message: 自定义消息处理回调
        auto_reply: 是否自动回复
        mention_required: 是否需要 @机器人 才响应

    Returns:
        消息处理函数
    """

    async def handler(event: FeishuMessageEvent) -> FeishuReply:
        """处理飞书消息事件。

        Args:
            event: 消息事件

        Returns:
            回复内容
        """
        _logger.debug("收到飞书消息: chat=%s, user=%s", event.chat_id, event.user_id)

        # 检查是否需要 @
        if mention_required and not event.is_mention:
            _logger.debug("消息未 @机器人，忽略")
            return FeishuReply(content="", type="skip")

        # 调用自定义处理器
        if on_message:
            return await on_message(event)

        # 默认回复
        if auto_reply:
            return FeishuReply(
                content=f"收到消息: {event.content[:100]}...",
                type="text",
            )

        return FeishuReply(content="", type="skip")

    return handler


__all__ = ["create_feishu_handler", "MessageHandler"]
