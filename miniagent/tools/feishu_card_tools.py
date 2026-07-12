"""飞书互动卡片工具：发送与更新 interactive 消息。

**重构说明**：配置检查使用 miniagent/tools/feishu_utils.py 的共享函数。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from miniagent.feishu.cards.builder import build_button, build_interactive_card
from miniagent.feishu.lark_client import config_from_env
from miniagent.feishu.receive_id import default_receive_id_for_send, effective_receive_id_type
from miniagent.tools.feishu_utils import check_lark_oapi
from miniagent.types.error_prefix import SUCCESS_PREFIX, WARNING_PREFIX
from miniagent.types.tool import ToolContext, ToolDefinition, ToolResult

_logger = logging.getLogger(__name__)

FEISHU_CARD_TOOL_NAMES = frozenset(
    {
        "feishu_send_interactive_card",
        "feishu_update_message_card",
    }
)


async def _feishu_send_interactive_card(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """构造并发送交互卡片，同时统一处理飞书 API 错误。"""
    cfg = config_from_env()
    if cfg is None:
        return ToolResult(success=False, content=f"{WARNING_PREFIX} 未配置 FEISHU_APP_ID / FEISHU_APP_SECRET。")
    dep_err = check_lark_oapi()
    if dep_err:
        return dep_err

    receive_id, recv_err = default_receive_id_for_send(args, ctx)
    if recv_err:
        return ToolResult(success=False, content=f"{WARNING_PREFIX} {recv_err}")
    if not receive_id:
        return ToolResult(success=False, content=f"{WARNING_PREFIX} 缺少 receive_id。")

    body = str(args.get("markdown_body") or args.get("body") or args.get("content") or "")
    header = str(args.get("header") or args.get("title") or "🤖 Mini Agent")
    template = str(args.get("template") or "blue")
    reply_to = str(args.get("reply_to_message_id") or "").strip() or None
    reply_in_thread = bool(args.get("reply_in_thread"))

    buttons_raw = args.get("buttons") or []
    buttons: list[dict[str, Any]] = []
    if isinstance(buttons_raw, list):
        for b in buttons_raw:
            if not isinstance(b, dict):
                continue
            label = str(b.get("label") or b.get("text") or "确定")
            btn_text = str(b.get("miniagent_text") or b.get("payload") or label)
            extra: dict[str, Any] = {}
            dk = str(b.get("dedupe_key") or "").strip()
            if dk:
                extra["dedupe_key"] = dk
            buttons.append(
                build_button(
                    label,
                    miniagent_text=btn_text,
                    chat_id=str(b.get("chat_id") or receive_id),
                    action_id=str(b.get("action_id") or "").strip() or None,
                    chat_type=str(b.get("chat_type") or "group"),
                    extra_value=extra or None,
                )
            )

    extra_elements: list[dict[str, Any]] | None = None
    raw_extra = args.get("extra_elements") or args.get("fields")
    if isinstance(raw_extra, list):
        extra_elements = [x for x in raw_extra if isinstance(x, dict)]
    elif isinstance(raw_extra, str) and raw_extra.strip():
        try:
            parsed = json.loads(raw_extra)
            if isinstance(parsed, list):
                extra_elements = [x for x in parsed if isinstance(x, dict)]
        except json.JSONDecodeError as e:
            _logger.debug("解析extra_elements失败: %s", e)

    card = build_interactive_card(
        header, body, template, buttons=buttons or None, extra_elements=extra_elements
    )
    card_json = json.dumps(card, ensure_ascii=False)

    from miniagent.feishu.im_send import post_im_message

    rid_type = effective_receive_id_type(args, ctx)
    ok, mid, err = post_im_message(
        cfg,
        receive_id=receive_id,
        msg_type="interactive",
        content_json=card_json,
        reply_to_message_id=reply_to,
        reply_in_thread=reply_in_thread,
        receive_id_type=rid_type,
    )
    if not ok:
        return ToolResult(success=False, content=f"{WARNING_PREFIX} 发送卡片失败: {err or 'unknown'}")
    return ToolResult(
        success=True, content=f"{SUCCESS_PREFIX} 已发送交互卡片。\n- message_id: {mid or '（未返回）'}"
    )


async def _feishu_update_message_card(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    _ = ctx
    cfg = config_from_env()
    if cfg is None:
        return ToolResult(success=False, content=f"{WARNING_PREFIX} 未配置 FEISHU_APP_ID / FEISHU_APP_SECRET。")
    dep_err = check_lark_oapi()
    if dep_err:
        return dep_err

    mid = str(args.get("message_id") or "").strip()
    if not mid:
        return ToolResult(success=False, content=f"{WARNING_PREFIX} 需要 message_id。")
    body = str(args.get("markdown_body") or args.get("body") or "")
    header = str(args.get("header") or args.get("title") or "🤖 Mini Agent")
    template = str(args.get("template") or "blue")
    card = build_interactive_card(header, body, template)
    card_json = json.dumps(card, ensure_ascii=False)

    try:
        import lark_oapi as lark
        from lark_oapi.api.im.v1 import PatchMessageRequest, PatchMessageRequestBody

        client = lark.Client.builder().app_id(cfg.app_id).app_secret(cfg.app_secret).build()
        body_req = PatchMessageRequestBody.builder().content(card_json).build()
        resp = client.im.v1.message.patch(
            PatchMessageRequest.builder().message_id(mid).request_body(body_req).build()
        )
        if not resp.success():
            from miniagent.feishu.lark_response import format_lark_response_error

            return ToolResult(
                success=False, content=f"{WARNING_PREFIX} 更新失败: {format_lark_response_error(resp)}"
            )
    except Exception as e:
        return ToolResult(success=False, content=f"{WARNING_PREFIX} 更新异常: {e}")
    return ToolResult(success=True, content=f"{SUCCESS_PREFIX} 已更新消息卡片: {mid}")


_send_schema = {
    "type": "function",
    "function": {
        "name": "feishu_send_interactive_card",
        "description": "发送飞书 interactive 交互卡片（lark_md 正文，可选按钮触发卡片回调）。",
        "parameters": {
            "type": "object",
            "properties": {
                "markdown_body": {"type": "string"},
                "header": {"type": "string"},
                "template": {"type": "string", "description": "blue/green/red 等"},
                "buttons": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "miniagent_text": {"type": "string"},
                            "action_id": {"type": "string"},
                            "chat_id": {"type": "string"},
                            "chat_type": {"type": "string"},
                            "dedupe_key": {"type": "string", "description": "防连点幂等键"},
                        },
                    },
                },
                "extra_elements": {
                    "description": "额外卡片元素 JSON 数组（v1 schema）",
                },
                "fields": {"description": "extra_elements 别名"},
                "receive_id": {"type": "string"},
                "receive_id_type": {"type": "string"},
                "reply_to_message_id": {"type": "string"},
                "reply_in_thread": {"type": "boolean"},
            },
            "required": ["markdown_body"],
        },
    },
}

_update_schema = {
    "type": "function",
    "function": {
        "name": "feishu_update_message_card",
        "description": "PATCH 更新已有飞书 interactive 消息卡片内容。",
        "parameters": {
            "type": "object",
            "properties": {
                "message_id": {"type": "string"},
                "markdown_body": {"type": "string"},
                "header": {"type": "string"},
                "template": {"type": "string"},
            },
            "required": ["message_id", "markdown_body"],
        },
    },
}

feishu_card_tools: dict[str, ToolDefinition] = {
    "feishu_send_interactive_card": ToolDefinition(
        schema=_send_schema,
        handler=_feishu_send_interactive_card,
        permission="allowlist",
        help_text="发送飞书交互卡片",
        toolbox="feishu",
    ),
    "feishu_update_message_card": ToolDefinition(
        schema=_update_schema,
        handler=_feishu_update_message_card,
        permission="allowlist",
        help_text="更新飞书交互卡片",
        toolbox="feishu",
    ),
}

__all__ = ["FEISHU_CARD_TOOL_NAMES", "feishu_card_tools"]
