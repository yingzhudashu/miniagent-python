"""飞书交互卡片与纯文本的最终出站投递。"""

from __future__ import annotations

import json
from typing import Any

from miniagent.feishu import card_rendering as _card_rendering
from miniagent.feishu.types import FeishuConfig
from miniagent.infrastructure.json_config import get_config
from miniagent.infrastructure.logger import get_logger

_logger = get_logger(__name__)
feishu_card_body_max = _card_rendering.feishu_card_body_max
_normalize_im_receive_chat_id = _card_rendering.normalize_im_receive_chat_id
_is_valid_im_receive_id = _card_rendering.is_valid_im_receive_id
_prepare_card_markdown = _card_rendering.prepare_card_markdown
_strip_light_markdown_for_feishu_plain = _card_rendering.strip_light_markdown_for_plain


def _feishu_interactive_card_dict(
    header_title: str, body_markdown: str, template: str
) -> dict[str, Any]:
    """构造飞书交互卡片 JSON 结构。"""
    from miniagent.feishu.cards.builder import build_interactive_card

    return build_interactive_card(header_title, body_markdown, template)


def _chunk_feishu_card_markdown(
    reply: str,
    max_len: int | None = None,
    *,
    already_normalized: bool = False,
) -> list[str]:
    """按当前卡片上限分块，并保持代码围栏闭合。"""
    cap = feishu_card_body_max() if max_len is None else max_len
    return _card_rendering.chunk_card_markdown(
        reply,
        cap,
        already_normalized=already_normalized,
    )


def _post_interactive_message(
    config: FeishuConfig,
    *,
    receive_id: str,
    card_json: str,
    reply_to_message_id: str | None = None,
    reply_in_thread: bool = False,
) -> tuple[bool, str | None]:
    """发送一条 ``msg_type=interactive``；成功返回 ``(True, message_id)``。"""
    from miniagent.feishu.im_send import post_im_message

    ok, mid, err = post_im_message(
        config,
        receive_id=receive_id,
        msg_type="interactive",
        content_json=card_json,
        reply_to_message_id=reply_to_message_id,
        reply_in_thread=reply_in_thread,
    )
    if not ok:
        _logger.warning("发送 interactive 失败: %s", err or "?")
        return False, None
    if not mid:
        _logger.warning("发送 interactive 成功但未返回 message_id")
        return False, None
    return True, mid


async def _post_interactive_message_async(
    config: FeishuConfig,
    *,
    receive_id: str,
    card_json: str,
    reply_to_message_id: str | None = None,
    reply_in_thread: bool = False,
    timeout: float = 30.0,
) -> tuple[bool, str | None]:
    """异步发送一条 ``msg_type=interactive``；成功返回 ``(True, message_id)``。

    使用 asyncio.to_thread 包装同步 SDK 调用，避免阻塞事件循环。
    这是流式输出丝滑的关键：创建思考卡片时不阻塞 LLM 流式处理。
    """
    from miniagent.feishu.im_send import post_im_message_async

    ok, mid, err = await post_im_message_async(
        config,
        receive_id=receive_id,
        msg_type="interactive",
        content_json=card_json,
        reply_to_message_id=reply_to_message_id,
        reply_in_thread=reply_in_thread,
        timeout=timeout,
    )
    if not ok:
        _logger.warning("发送 interactive 失败: %s", err or "?")
        return False, None
    if not mid:
        _logger.warning("发送 interactive 成功但未返回 message_id")
        return False, None
    return True, mid


def _post_text_message(
    config: FeishuConfig,
    *,
    receive_id: str,
    text_content_json: str,
    reply_to_message_id: str | None = None,
    reply_in_thread: bool = False,
) -> bool:
    """发送一条 ``msg_type=text``；成功返回 True。"""
    from miniagent.feishu.im_send import post_im_message

    ok, _mid, err = post_im_message(
        config,
        receive_id=receive_id,
        msg_type="text",
        content_json=text_content_json,
        reply_to_message_id=reply_to_message_id,
        reply_in_thread=reply_in_thread,
    )
    if not ok:
        _logger.warning("发送 text 失败: %s", err or "?")
    return ok


def _feishu_reply_plain_enabled() -> bool:
    """``MINIAGENT_FEISHU_REPLY_PLAIN``：默认渲染富文本 Markdown；设为 ``1`` 时去掉常见 Markdown 标记（仍为 ``lark_md``）。"""
    return bool(get_config("feishu.reply_plain", False))




def _send_interactive_reply_cards(
    config: FeishuConfig,
    cid: str,
    parts: list[str],
    *,
    reply_to_message_id: str | None = None,
    reply_in_thread: bool = False,
    already_normalized: bool = False,
) -> tuple[int, int]:
    """发送多条交互卡片回复。返回 (已成功条数, 总条数)；任一分片失败即中止后续分片。"""
    n = len(parts)
    if n == 0:
        return (0, 0)
    sent = 0
    for i, part in enumerate(parts):
        body = _prepare_card_markdown(part, normalize=not already_normalized)
        title = "🤖 Mini Agent" if n == 1 else f"🤖 Mini Agent ({i + 1}/{n})"
        card = _feishu_interactive_card_dict(title, body, "blue")
        card_json = json.dumps(card, ensure_ascii=False)
        ok, _mid = _post_interactive_message(
            config,
            receive_id=cid,
            card_json=card_json,
            reply_to_message_id=reply_to_message_id,
            reply_in_thread=reply_in_thread,
        )
        if not ok:
            _logger.warning("发送回复失败 (%s/%s)", i + 1, n)
            return (sent, n)
        sent += 1
    return (sent, n)


def _send_plain_text_chunks(
    config: FeishuConfig,
    cid: str,
    text: str,
    *,
    reason: str | None = None,
    reply_to_message_id: str | None = None,
    reply_in_thread: bool = False,
) -> None:
    """interactive 不可用或需短提示时，按正文上限分条发送纯文本（无 Markdown 渲染）。"""
    if reason:
        _logger.warning(
            "飞书发送 msg_type=text 回退（无 lark_md 渲染）: reason=%s chat_id_prefix=%s",
            reason,
            (cid or "")[:12],
        )
    try:
        chunks = _chunk_feishu_card_markdown(text or "")
        if not chunks:
            return
        for i, ch in enumerate(chunks):
            payload = json.dumps({"text": ch}, ensure_ascii=False)
            ok = _post_text_message(
                config,
                receive_id=cid,
                text_content_json=payload,
                reply_to_message_id=reply_to_message_id,
                reply_in_thread=reply_in_thread,
            )
            if not ok:
                _logger.warning("发送文本回退失败 (%s/%s)", i + 1, len(chunks))
                break
    except Exception as e:
        _logger.debug("文本回退跳过: %s", e)


async def _send_reply(
    config: FeishuConfig,
    chat_id: str,
    reply: str,
    *,
    reply_to_message_id: str | None = None,
    reply_in_thread: bool = False,
) -> None:
    """通过飞书 API 发送回复（交互式卡片 + lark_md，与思考卡片同一套构建逻辑）。"""
    cid = _normalize_im_receive_chat_id(chat_id)
    if not _is_valid_im_receive_id(cid):
        _logger.debug("跳过发送回复：无效的 chat_id (%s)", chat_id)
        return

    body = reply or ""
    if _feishu_reply_plain_enabled():
        body = _strip_light_markdown_for_feishu_plain(body)
    parts = _chunk_feishu_card_markdown(body)
    n = len(parts)
    sent = 0
    try:
        sent, _ = _send_interactive_reply_cards(
            config,
            cid,
            parts,
            reply_to_message_id=reply_to_message_id,
            reply_in_thread=reply_in_thread,
            already_normalized=True,
        )
    except ImportError:
        _logger.error("请安装 lark-oapi: pip install lark-oapi")
        _send_plain_text_chunks(
            config,
            cid,
            reply or "",
            reason="lark_oapi_import_error",
            reply_to_message_id=reply_to_message_id,
            reply_in_thread=reply_in_thread,
        )
        return
    except Exception as e:
        _logger.error("发送回复异常: %s", e)
        sent = 0

    if sent >= n:
        return
    if sent > 0:
        notice = (
            f"（Mini Agent：本回复共分 {n} 段，已成功发送前 {sent} 段；"
            "剩余段落未能送达。完整内容见本会话的 history.json。）"
        )
        _send_plain_text_chunks(
            config,
            cid,
            notice,
            reason="partial_card_send_notice",
            reply_to_message_id=reply_to_message_id,
            reply_in_thread=reply_in_thread,
        )
        return
    _send_plain_text_chunks(
        config,
        cid,
        reply or "",
        reason="interactive_reply_failed_full_fallback",
        reply_to_message_id=reply_to_message_id,
        reply_in_thread=reply_in_thread,
    )


__all__ = [
    "_feishu_reply_plain_enabled",
    "_post_interactive_message",
    "_post_interactive_message_async",
    "_post_text_message",
    "_send_interactive_reply_cards",
    "_send_plain_text_chunks",
    "_send_reply",
]
