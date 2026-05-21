"""飞书 IM 出站 ``receive_id`` 解析（工具与卡片共用）。"""

from __future__ import annotations

from typing import Any

from miniagent.feishu.im_send import resolve_im_receive_id_type
from miniagent.types.tool import ToolContext


def effective_receive_id_type(args: dict[str, Any], ctx: ToolContext) -> str:
    a = str(args.get("receive_id_type") or "").strip().lower()
    if a in ("chat_id", "open_id", "union_id"):
        return a
    c = (ctx.feishu_im_receive_id_type or "").strip().lower()
    if c in ("chat_id", "open_id", "union_id"):
        return c
    return resolve_im_receive_id_type(None)


def default_receive_id_for_send(args: dict[str, Any], ctx: ToolContext) -> tuple[str | None, str | None]:
    explicit = str(args.get("receive_id") or "").strip()
    if explicit:
        return explicit, None
    rid_t = effective_receive_id_type(args, ctx)
    if rid_t == "chat_id":
        mid = (ctx.message_queue_abort_chat_id or "").strip()
        if mid:
            return mid, None
        return None, "当前非飞书会话（无 chat_id），且未传入 receive_id。"
    alt = (getattr(ctx, "feishu_im_receive_id", None) or "").strip()
    if alt:
        return alt, None
    return None, (
        "缺少与 receive_id_type 匹配的 receive_id：请传入 receive_id，"
        "或确保飞书入站已注入发送者 ID（feishu_im_receive_id，通常为 open_id）。"
    )


__all__ = ["default_receive_id_for_send", "effective_receive_id_type"]
