"""Agent tools for the single-account Xianyu runtime."""

from __future__ import annotations

import json
from typing import Any

from miniagent.agent.types.error_prefix import ERROR_PREFIX, SUCCESS_PREFIX
from miniagent.agent.types.tool import ToolContext, ToolDefinition, ToolResult
from miniagent.assistant.tools.base import tool
from miniagent.assistant.tools.path_utils import resolve_path_for_tool
from miniagent.assistant.xianyu.runtime import get_xianyu_runtime

XIANYU_TOOL_NAMES = frozenset(
    {
        "xianyu_get_status",
        "xianyu_get_item",
        "xianyu_get_history",
        "xianyu_upload_image",
        "xianyu_send_text",
        "xianyu_send_image",
        "xianyu_create_chat",
        "xianyu_publish_item",
    }
)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _runtime_client():
    runtime = get_xianyu_runtime()
    if runtime.client is None:
        raise RuntimeError("闲鱼未配置或尚未启动")
    return runtime, runtime.client


async def _status(_args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
    try:
        status = get_xianyu_runtime().status()
        return ToolResult(
            True,
            _json(
                {
                    "enabled": status.enabled,
                    "connected": status.connected,
                    "authenticated": status.authenticated,
                    "paused": status.paused,
                    "owner_id": status.owner_id,
                    "last_error": status.last_error,
                    "reconnect_attempt": status.reconnect_attempt,
                }
            ),
        )
    except Exception as error:
        return ToolResult(False, f"{ERROR_PREFIX} {error}")


async def _get_item(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
    try:
        _runtime, client = _runtime_client()
        return ToolResult(True, _json(await client.get_item(str(args["item_id"]))))
    except Exception as error:
        return ToolResult(False, f"{ERROR_PREFIX} 获取商品失败: {error}")


async def _get_history(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
    try:
        runtime, _client = _runtime_client()
        return ToolResult(True, _json(await runtime.get_history(str(args["conversation_id"]))))
    except Exception as error:
        return ToolResult(False, f"{ERROR_PREFIX} 获取聊天记录失败: {error}")


def _path(
    args: dict[str, Any], ctx: ToolContext, key: str = "path"
) -> tuple[str | None, ToolResult | None]:
    return resolve_path_for_tool(str(args[key]), ctx)


async def _upload_image(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    path, error = _path(args, ctx)
    if error:
        return error
    try:
        _runtime, client = _runtime_client()
        return ToolResult(True, _json(await client.upload_image(path or "")))
    except Exception as exc:
        return ToolResult(False, f"{ERROR_PREFIX} 上传图片失败: {exc}")


async def _send_text(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
    try:
        runtime, _client = _runtime_client()
        await runtime.send_text(
            str(args["conversation_id"]), str(args["receiver_id"]), str(args["text"])
        )
        return ToolResult(True, f"{SUCCESS_PREFIX} 闲鱼文字消息已发送")
    except Exception as error:
        return ToolResult(False, f"{ERROR_PREFIX} 发送失败: {error}")


async def _send_image(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    path, error = _path(args, ctx)
    if error:
        return error
    try:
        runtime, client = _runtime_client()
        image = await client.upload_image(path or "")
        await runtime.send_image(
            str(args["conversation_id"]),
            str(args["receiver_id"]),
            url=image["url"],
            width=image["width"],
            height=image["height"],
        )
        return ToolResult(True, f"{SUCCESS_PREFIX} 闲鱼图片消息已发送\n{_json(image)}")
    except Exception as exc:
        return ToolResult(False, f"{ERROR_PREFIX} 发送图片失败: {exc}")


async def _create_chat(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
    try:
        runtime, _client = _runtime_client()
        conversation_id = await runtime.create_chat(str(args["receiver_id"]), str(args["item_id"]))
        return ToolResult(True, _json({"conversation_id": conversation_id}))
    except Exception as error:
        return ToolResult(False, f"{ERROR_PREFIX} 主动建聊失败: {error}")


async def _publish_item(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    paths: list[str] = []
    for raw in args.get("image_paths") or []:
        resolved, error = resolve_path_for_tool(str(raw), ctx)
        if error:
            return error
        assert resolved is not None
        paths.append(resolved)
    try:
        _runtime, client = _runtime_client()
        result = await client.publish_item(
            image_paths=paths,
            description=str(args["description"]),
            delivery=str(args["delivery"]),  # type: ignore[arg-type]
            longitude=args["longitude"],
            latitude=args["latitude"],
            current_price=args.get("current_price"),
            original_price=args.get("original_price"),
            shipping_fee=args.get("shipping_fee"),
            self_pickup=bool(args.get("self_pickup", False)),
        )
        return ToolResult(True, _json(result))
    except Exception as error:
        return ToolResult(False, f"{ERROR_PREFIX} 发布商品失败: {error}")


xianyu_tools: dict[str, ToolDefinition] = {
    "xianyu_get_status": tool("xianyu_get_status", "查看闲鱼登录、连接、暂停和重连状态。")
    .allowlist()
    .toolbox("xianyu")
    .handler(_status)
    .build(),
    "xianyu_get_item": tool("xianyu_get_item", "按商品 ID 获取闲鱼商品详情。")
    .param("item_id", "string", "闲鱼商品 ID")
    .allowlist()
    .toolbox("xianyu")
    .handler(_get_item)
    .build(),
    "xianyu_get_history": tool("xianyu_get_history", "获取指定闲鱼会话的全部历史消息。")
    .param("conversation_id", "string", "闲鱼会话 ID")
    .allowlist()
    .toolbox("xianyu")
    .handler(_get_history)
    .build(),
    "xianyu_upload_image": tool("xianyu_upload_image", "上传工作区内图片到闲鱼媒体服务。")
    .param("path", "string", "工作区内图片路径")
    .allowlist()
    .toolbox("xianyu")
    .handler(_upload_image)
    .build(),
    "xianyu_send_text": tool("xianyu_send_text", "向已有闲鱼会话发送文字消息。")
    .param("conversation_id", "string", "会话 ID")
    .param("receiver_id", "string", "接收者闲鱼用户 ID")
    .param("text", "string", "消息内容")
    .allowlist()
    .toolbox("xianyu")
    .handler(_send_text)
    .build(),
    "xianyu_send_image": tool("xianyu_send_image", "上传工作区图片并发送到已有闲鱼会话。")
    .param("conversation_id", "string", "会话 ID")
    .param("receiver_id", "string", "接收者闲鱼用户 ID")
    .param("path", "string", "工作区内图片路径")
    .allowlist()
    .toolbox("xianyu")
    .handler(_send_image)
    .build(),
    "xianyu_create_chat": tool("xianyu_create_chat", "按买家 ID 和商品 ID 主动创建闲鱼会话。")
    .param("receiver_id", "string", "买家闲鱼用户 ID")
    .param("item_id", "string", "关联商品 ID")
    .require_confirm()
    .toolbox("xianyu")
    .handler(_create_chat)
    .build(),
    "xianyu_publish_item": tool("xianyu_publish_item", "发布一个闲鱼商品；位置必须显式提供。")
    .array_param("image_paths", "工作区内商品图片路径")
    .param("description", "string", "商品标题与描述")
    .enum_param(
        "delivery",
        "配送方式",
        ["free_shipping", "distance_based", "fixed", "pickup_only"],
    )
    .param("longitude", "number", "发布位置经度")
    .param("latitude", "number", "发布位置纬度")
    .optional("current_price", "number", "现价，按 Decimal 精确转分")
    .optional("original_price", "number", "原价，按 Decimal 精确转分")
    .optional("shipping_fee", "number", "固定运费；delivery=fixed 时必填")
    .optional("self_pickup", "boolean", "是否同时允许自提")
    .require_confirm()
    .toolbox("xianyu")
    .handler(_publish_item)
    .build(),
}

__all__ = ["XIANYU_TOOL_NAMES", "xianyu_tools"]
