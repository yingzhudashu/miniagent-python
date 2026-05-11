"""Mini Agent Python — Feishu 服务器（兼容层）

提供飞书 WebSocket 服务器的创建接口。
此模块为兼容层，实际实现位于 poll_server.py。

新集成优先阅读 ``docs/FEISHU.md`` 再选用 Webhook 或长轮询形态。

导出：
- create_feishu_server(): 创建飞书服务器实例（返回异步启动函数）
"""

from __future__ import annotations

from typing import Any, Callable

from miniagent.feishu.poll_server import start_feishu_poll_server
from miniagent.feishu.types import FeishuConfig
from miniagent.infrastructure.message_queue import MessageQueueManager


def create_feishu_server(
    app_id: str = "",
    app_secret: str = "",
    *,
    verify_token: str = "",
    encrypt_key: str = "",
    on_message: Any = None,
) -> Callable:
    """创建飞书服务器实例。

    Args:
        app_id: 飞书应用 ID
        app_secret: 飞书应用密钥
        verify_token: 验证令牌
        encrypt_key: 加密密钥
        on_message: 消息处理回调（传递给 start_feishu_poll_server）

    Returns:
        异步启动函数（调用后开始轮询）
    """
    config = FeishuConfig(
        app_id=app_id,
        app_secret=app_secret,
        verify_token=verify_token,
        encrypt_key=encrypt_key,
    )

    async def start() -> None:
        """启动飞书轮询服务器。"""
        mq = MessageQueueManager()
        await start_feishu_poll_server(config, on_message, message_queue=mq)

    return start


__all__ = ["create_feishu_server"]
