"""飞书 IM 消息发送（create / reply）共用实现，供 ``poll_server`` 与 ``upload_io`` 调用。

性能优化：
- 提供异步版本 ``post_im_message_async()`` 包装同步 SDK 调用，避免阻塞事件循环
- 建议在异步上下文（async def）中使用异步版本
"""

from __future__ import annotations

import asyncio
from typing import Literal

from miniagent.agent.constants import FEISHU_PATCH_TIMEOUT_S, FEISHU_SEND_TIMEOUT
from miniagent.agent.logging import get_logger
from miniagent.assistant.feishu.lark_client import build_client, clear_client_cache
from miniagent.assistant.feishu.lark_response import format_lark_response_error
from miniagent.assistant.feishu.types import FeishuConfig
from miniagent.assistant.infrastructure.json_config import get_config

_logger = get_logger(__name__)

ImMsgType = Literal["text", "file", "image", "interactive"]

_VALID_RECEIVE_ID_TYPES = frozenset({"chat_id", "open_id", "union_id"})

_FEISHU_SEND_TIMEOUT_DEFAULT = FEISHU_SEND_TIMEOUT


def _feishu_patch_timeout_default() -> float:
    return float(FEISHU_PATCH_TIMEOUT_S)


def resolve_im_receive_id_type(explicit: str | None) -> str:
    """解析 ``receive_id_type``：显式参数优先，否则从JSON配置读取，默认 ``chat_id``。"""
    raw = (explicit or "").strip().lower()
    if raw in _VALID_RECEIVE_ID_TYPES:
        return raw
    # 从JSON配置获取（支持环境变量覆盖）
    env = get_config("feishu.receive_id_type", "chat_id")
    if isinstance(env, str) and env.strip().lower() in _VALID_RECEIVE_ID_TYPES:
        return env.strip().lower()
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

        client = build_client(config)
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


async def post_im_message_async(
    config: FeishuConfig,
    *,
    receive_id: str,
    msg_type: ImMsgType,
    content_json: str,
    reply_to_message_id: str | None = None,
    reply_in_thread: bool = False,
    receive_id_type: str | None = None,
    timeout: float = _FEISHU_SEND_TIMEOUT_DEFAULT,
) -> tuple[bool, str | None, str | None]:
    """异步发送 IM 消息（不阻塞事件循环）。

    使用 ``asyncio.to_thread()`` 包装同步 SDK 调用，
    避免在异步上下文中阻塞事件循环。

    Args:
        config: 飞书配置
        receive_id: 接收者 ID
        msg_type: 消息类型
        content_json: 消息内容 JSON
        reply_to_message_id: 回复的消息 ID
        reply_in_thread: 是否在话题内回复
        receive_id_type: 接收者 ID 类型
        timeout: 超时秒数（默认 30 秒）

    Returns:
        ``(success, message_id_or_None, error_detail_or_None)``；失败时 ``error_detail`` 含错误信息。

    Example:
        success, mid, err = await post_im_message_async(
            config,
            receive_id="oc_xxx",
            msg_type="text",
            content_json=json.dumps({"text": "Hello"}),
        )
    """
    def _sync_send() -> tuple[bool, str | None, str | None]:
        return post_im_message(
            config,
            receive_id=receive_id,
            msg_type=msg_type,
            content_json=content_json,
            reply_to_message_id=reply_to_message_id,
            reply_in_thread=reply_in_thread,
            receive_id_type=receive_id_type,
        )

    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_sync_send),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        _logger.warning("Feishu IM send timed out after %s seconds", timeout)
        return False, None, f"timeout_{timeout}s"
    except Exception as e:
        _logger.warning("Feishu IM async send 异常: %s", e)
        return False, None, str(e)


def patch_im_message(
    config: FeishuConfig,
    *,
    message_id: str,
    content_json: str,
) -> tuple[bool, str | None]:
    """PATCH 更新已有 IM 消息内容（同步版本）。

    Args:
        config: 飞书配置
        message_id: 要更新的消息 ID
        content_json: 新消息内容 JSON

    Returns:
        ``(success, error_detail_or_None)``；失败时 ``error_detail`` 含错误信息。
    """
    try:
        import lark_oapi as lark
        from lark_oapi.api.im.v1 import PatchMessageRequest, PatchMessageRequestBody

        client = lark.Client.builder().app_id(config.app_id).app_secret(config.app_secret).build()
        body = PatchMessageRequestBody.builder().content(content_json).build()
        request = PatchMessageRequest.builder().message_id(message_id).request_body(body).build()
        response = client.im.v1.message.patch(request)
        if response.success():
            return True, None
        err = format_lark_response_error(response)
        _logger.warning("Feishu IM PATCH failed: %s", err)
        return False, err
    except ImportError:
        _logger.error("请安装 lark-oapi: pip install lark-oapi")
        return False, "missing_lark_oapi"
    except Exception as e:
        _logger.warning("Feishu IM PATCH 异常: %s", e)
        return False, str(e)


async def patch_im_message_async(
    config: FeishuConfig,
    *,
    message_id: str,
    content_json: str,
    timeout: float | None = None,
) -> tuple[bool, str | None]:
    """异步 PATCH 更新 IM 消息（不阻塞事件循环）。

    使用 ``asyncio.to_thread()`` 包装同步 SDK 调用，
    避免在异步上下文中阻塞事件循环。

    这是流式输出丝滑的关键：PATCH 更新飞书思考卡片时，
    不会阻塞 LLM 流式处理，用户感知卡片实时更新。

    Args:
        config: 飞书配置
        message_id: 要更新的消息 ID
        content_json: 新消息内容 JSON
        timeout: 超时秒数（默认 10 秒，比发送更短）

    Returns:
        ``(success, error_detail_or_None)``；失败时 ``error_detail`` 含错误信息。

    Example:
        ok, err = await patch_im_message_async(
            config,
            message_id="om_xxx",
            content_json=json.dumps(card_dict),
        )
    """
    if timeout is None:
        timeout = _feishu_patch_timeout_default()

    def _sync_patch() -> tuple[bool, str | None]:
        return patch_im_message(
            config,
            message_id=message_id,
            content_json=content_json,
        )

    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_sync_patch),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        _logger.warning("Feishu IM PATCH timed out after %s seconds", timeout)
        return False, f"timeout_{timeout}s"
    except Exception as e:
        _logger.warning("Feishu IM async PATCH 异常: %s", e)
        return False, str(e)


__all__ = [
    "ImMsgType",
    "clear_client_cache",
    "resolve_im_receive_id_type",
    "post_im_message",
    "post_im_message_async",
    "patch_im_message",
    "patch_im_message_async",
]
