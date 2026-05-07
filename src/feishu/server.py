"""Mini Agent Python — 飞书 Webhook HTTP 服务器 (Phase 8)

接收飞书开放平台的事件推送，处理 URL 验证和消息事件。

与长轮询模式的区别：
- Webhook 模式（本文件）：需要公网可达的 URL，飞书主动推送事件
- 长轮询模式（poll_server.py）：主动连接飞书服务器，无需公网 IP

工作流程：
1. 飞书开放平台 → POST /webhook → 验证 challenge
2. 收到消息事件 → 解析 → 路由到 handler
3. handler 调用 Agent → 获取回复 → 通过飞书 API 发送回复
"""

from __future__ import annotations

import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, Awaitable, Callable

from src.feishu.types import FeishuConfig
from src.core.logger import get_logger

_logger = get_logger(__name__)


async def _send_reply(config: FeishuConfig, chat_id: str, reply: str) -> None:
    """通过飞书 API 发送回复。"""
    try:
        import lark_oapi as lark
        from lark_oapi.api.im.v1 import (
            CreateMessageRequest,
            CreateMessageRequestBody,
        )

        client = lark.Client.builder().app_id(config.app_id).app_secret(config.app_secret).build()

        request = CreateMessageRequest.builder() \
            .receive_id_type("chat_id") \
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("text")
                .content(json.dumps({"text": reply}))
                .build()
            ) \
            .build()

        response = client.im.v1.message.create(request)
        if response.success():
            _logger.debug("已回复 [%s]", chat_id)
        else:
            _logger.warning("发送回复失败: %s %s", response.code, response.msg)

    except ImportError:
        _logger.error("请安装 lark-oapi: pip install lark-oapi")
    except Exception as e:
        _logger.error("回复失败 [%s]: %s", chat_id, e)


async def _handle_event(
    data: dict[str, Any],
    config: FeishuConfig,
    message_handler: Callable[[str, str, str], Awaitable[str]],
) -> None:
    """处理飞书事件（内部函数）。"""
    event = data.get("event")
    if not event:
        return

    header = event.get("header", {})
    event_type = header.get("event_type", "")

    # 只处理消息事件
    if event_type != "im.message.receive_v1":
        return

    message = event.get("message")
    sender = event.get("sender")
    if not message or not sender:
        return

    msg_type = message.get("message_type", message.get("msg_type", ""))
    chat_id = event.get("chat_id", message.get("chat_id", ""))
    sender_id = sender.get("sender_id", {}).get("open_id", "")
    message_id = message.get("message_id", "")

    # 去重检查
    from src.feishu.poll_server import try_begin_processing, release_processing

    if message_id and not try_begin_processing(message_id):
        _logger.debug("跳过重复消息: %s", message_id)
        return

    # 只处理文本消息
    if msg_type != "text":
        _logger.debug("忽略非文本消息: %s", msg_type)
        if message_id:
            release_processing(message_id)
        return

    content = message.get("content", "")
    text = ""
    try:
        parsed = json.loads(content)
        text = parsed.get("text", "")
    except (json.JSONDecodeError, TypeError):
        text = content

    if not text.strip():
        if message_id:
            release_processing(message_id)
        return

    _logger.info("收到消息 [%s] %s: %s", chat_id, sender_id, text)

    try:
        reply = await message_handler(text, chat_id, sender_id)
        if reply:
            await _send_reply(config, chat_id, reply)
    except Exception as e:
        _logger.error("处理消息失败: %s", e)
    finally:
        if message_id:
            release_processing(message_id)


def create_feishu_server(
    config: FeishuConfig,
    message_handler: Callable[[str, str, str], Awaitable[str]],
) -> HTTPServer:
    """创建飞书 Webhook HTTP 服务器。

    Args:
        config: 飞书应用配置
        message_handler: 消息处理函数 (content, chatId, senderId) => reply

    Returns:
        HTTPServer 实例，调用 .serve_forever() 后开始监听
    """

    class FeishuHandler(BaseHTTPRequestHandler):
        """处理飞书 Webhook 请求。"""

        def do_POST(self):
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")

            try:
                data = json.loads(body)

                # 处理 URL 验证 (challenge)
                if data.get("type") == "url_verification":
                    _logger.info("收到 URL 验证请求")
                    challenge = data.get("challenge", "")
                    response_body = json.dumps({"challenge": challenge}).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(response_body)
                    return

                # 处理事件
                if data.get("event"):
                    import asyncio
                    asyncio.run(_handle_event(data, config, message_handler))

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"code": 0}).encode("utf-8"))

            except Exception as e:
                _logger.error("处理请求失败: %s", e)
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b"Internal Server Error")

        def do_GET(self):
            self.send_response(405)
            self.end_headers()
            self.wfile.write(b"Method Not Allowed")

        def log_message(self, format, *args):
            # 静默日志
            pass

    server = HTTPServer(("0.0.0.0", config.port), FeishuHandler)
    return server


def start_feishu_server(
    config: FeishuConfig,
    message_handler: Callable[[str, str, str], Awaitable[str]],
) -> HTTPServer:
    """创建并启动飞书 Webhook 服务器。

    Args:
        config: 飞书应用配置
        message_handler: 消息处理函数

    Returns:
        已启动的 HTTPServer 实例
    """
    server = create_feishu_server(config, message_handler)

    _logger.info("飞书 Webhook 服务器已启动: http://0.0.0.0:%d/webhook", config.port)
    _logger.info("请在飞书开放平台配置请求地址: https://your-domain:%d/webhook", config.port)

    return server


__all__ = ["create_feishu_server", "start_feishu_server"]
