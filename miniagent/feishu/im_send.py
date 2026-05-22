"""飞书 IM 消息发送（create / reply）共用实现，供 ``poll_server`` 与 ``upload_io`` 调用。"""

from __future__ import annotations

import os
from typing import Any, Literal

from miniagent.feishu.lark_response import format_lark_response_error
from miniagent.feishu.types import FeishuConfig
from miniagent.infrastructure.logger import get_logger

_logger = get_logger(__name__)

ImMsgType = Literal["text", "file", "image", "interactive"]

_VALID_RECEIVE_ID_TYPES = frozenset({"chat_id", "open_id", "union_id"})

# 客户端缓存（按 app_id 复用，避免每次发送都重建连接）
_client_cache: dict[str, Any] = {}


def _get_lark_client(config: FeishuConfig) -> Any:
    """获取或复用已缓存的 Lark SDK 客户端。"""
    try:
        import lark_oapi as lark
    except ImportError:
        raise ImportError("请安装 lark-oapi: pip install lark-oapi")

    key = config.app_id
    if key not in _client_cache:
        _client_cache[key] = (
            lark.Client.builder().app_id(config.app_id).app_secret(config.app_secret).build()
        )
    return _client_cache[key]


def clear_client_cache() -> None:
    """清除客户端缓存（测试用）。"""
    _client_cache.clear()


def resolve_im_receive_id_type(explicit: str | None) -> str:
    """解析 ``receive_id_type``：显式参数优先，否则读 ``MINIAGENT_FEISHU_RECEIVE_ID_TYPE``，默认 ``chat_id``。"""
    raw = (explicit or "").strip().lower()
    if raw in _VALID_RECEIVE_ID_TYPES:
        return raw
    env = (os.environ.get("MINIAGENT_FEISHU_RECEIVE_ID_TYPE") or "").strip().lower()
    if env in _VALID_RECEIVE_ID_TYPES:
        return env
    return "chat_id"


def post_im_message(
    config: FeishuConfig,
    *,
    receive_id: str,
    msg_type: ImMsgType,
    content_json: str,
    reply_to_message_id: str | None = None,
    reply_in_thread: bool = False,
    receive_id_type: str | None = None,
) -> tuple[bool, str | None, str | None]:
    """发送一条 IM 消息（新建或回复）。

    Returns:
        ``(success, message_id_or_None, error_detail_or_None)``；失败时 ``error_detail`` 含开放平台 ``code``/``msg``。
    """
    try:
        from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

        client = _get_lark_client(config)
        if reply_to_message_id:
            from lark_oapi.api.im.v1 import ReplyMessageRequest, ReplyMessageRequestBody

            rb = ReplyMessageRequestBody.builder().msg_type(msg_type).content(content_json)
            if reply_in_thread:
                rb = rb.reply_in_thread(True)
            request = (
                ReplyMessageRequest.builder()
                .message_id(reply_to_message_id)
                .request_body(rb.build())
                .build()
            )
            response = client.im.v1.message.reply(request)
        else:
            rid_type = resolve_im_receive_id_type(receive_id_type)
            request = (
                CreateMessageRequest.builder()
                .receive_id_type(rid_type)
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(receive_id)
                    .msg_type(msg_type)
                    .content(content_json)
                    .build()
                )
                .build()
            )
            response = client.im.v1.message.create(request)
        if not response.success():
            err = format_lark_response_error(response)
            _logger.warning("Feishu IM %s failed: %s", msg_type, err)
            return False, None, err
        mid = None
        if response.data and getattr(response.data, "message_id", None):
            mid = response.data.message_id
        return True, mid, None
    except ImportError:
        _logger.error("请安装 lark-oapi: pip install lark-oapi")
        return False, None, "missing_lark_oapi"
    except Exception as e:
        _logger.warning("Feishu IM send 异常: %s", e)
        return False, None, str(e)
