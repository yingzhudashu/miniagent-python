"""飞书卡片流状态机与出站投递实现。"""

from __future__ import annotations

import json
from typing import Any

from miniagent.agent.constants import FEISHU_PATCH_IMPORTANT_CONTENT_IMMEDIATE
from miniagent.agent.logging import get_logger
from miniagent.assistant.feishu import card_rendering as _card_rendering
from miniagent.assistant.feishu.card_rendering import (
    FEISHU_THINKING_PATCH_BUDGET,
    FEISHU_THINKING_PATCH_MIN_CHAR_DELTA,
    FEISHU_THINKING_PATCH_MIN_INTERVAL_S,
)
from miniagent.assistant.feishu.cards.builder import build_interactive_card
from miniagent.assistant.feishu.outbound_delivery import (
    _post_interactive_message,
    _post_interactive_message_async,
)
from miniagent.ui.feishu.types import FeishuConfig

_logger = get_logger(__name__)

def _thinking_card_json_cached(
    st: Any,
    raw: str,
    template: str,
    session_key: str | None,
    confirmation_engine: Any | None = None,
) -> str:
    """为同一轮思考流缓存 normalized body 与 card JSON。

    流式 PATCH 会频繁检查是否需要更新卡片；当累计正文未变化时复用结果，避免重复
    `_normalize_lark_md()` 和 `json.dumps()`。
    """
    confirmation_pending = False
    if confirmation_engine is not None and session_key:
        channel = (
            confirmation_engine.get_confirmation_channel(session_key)
            if hasattr(confirmation_engine, "get_confirmation_channel")
            else getattr(confirmation_engine, "confirmation_channel", None)
        )
        confirmation_pending = bool(channel is not None and channel.has_pending)
    cache_key = (raw, template, session_key, confirmation_pending)
    if getattr(st, "feishu_cached_card_key", None) == cache_key:
        cached = getattr(st, "feishu_cached_card_json", None)
        if isinstance(cached, str):
            return cached
    cleaned = _card_rendering.prepare_thinking_markdown(raw)
    card_json = json.dumps(
        _thinking_interactive_card_dict(
            cleaned,
            template,
            session_key=session_key,
            confirmation_engine=confirmation_engine,
        ),
        ensure_ascii=False,
    )
    st.feishu_cached_card_key = cache_key
    st.feishu_cached_card_json = card_json
    return card_json


def _reset_feishu_thinking_cache(st: Any) -> None:
    """清理思考卡片渲染缓存。"""
    st.feishu_cached_card_key = None
    st.feishu_cached_card_json = None


def _reset_feishu_thinking_state(st: Any) -> None:
    """清理一轮思考流的全部累积状态（卡片 id、正文、节流计数、工具段、渲染缓存）。"""
    st.feishu_thinking_message_id = None
    st.feishu_stream_accumulated = ""
    st.feishu_last_patched_char_len = -1
    st.feishu_last_sent_card_json = None
    st.feishu_tool_section_started = False
    st.feishu_pending_tool_lines = []
    st.feishu_stream_llm_len = 0
    _reset_feishu_thinking_cache(st)


def _thinking_interactive_card_dict(
    cleaned_markdown: str,
    template: str,
    *,
    session_key: str | None = None,
    confirmation_engine: Any | None = None,
) -> dict[str, Any]:
    """构造思考内容交互卡片（可能包含确认按钮）。"""
    from miniagent.assistant.feishu.cards.builder import confirmation_buttons, thinking_card_dict

    buttons = None
    eng = confirmation_engine
    if eng is not None and session_key:
        cc_obj = (
            eng.get_confirmation_channel(session_key)
            if hasattr(eng, "get_confirmation_channel")
            else getattr(eng, "confirmation_channel", None)
        )
        if cc_obj is not None and cc_obj.has_pending:
            buttons = confirmation_buttons()

    return thinking_card_dict(cleaned_markdown, template, buttons=buttons)


def _create_interactive_thinking_message(
    config: FeishuConfig,
    chat_id: str,
    card_json: str,
    *,
    reply_to_message_id: str | None = None,
    reply_in_thread: bool = False,
) -> str | None:
    """创建交互式思考卡片，成功返回 message_id。"""
    ok, mid = _post_interactive_message(
        config,
        receive_id=chat_id,
        card_json=card_json,
        reply_to_message_id=reply_to_message_id,
        reply_in_thread=reply_in_thread,
    )
    if ok and mid:
        return mid
    return None


async def _create_interactive_thinking_message_async(
    config: FeishuConfig,
    chat_id: str,
    card_json: str,
    *,
    reply_to_message_id: str | None = None,
    reply_in_thread: bool = False,
    timeout: float = 30.0,
) -> str | None:
    """异步创建交互式思考卡片，成功返回 message_id。

    使用 asyncio.to_thread 包装同步 SDK 调用，避免阻塞事件循环。
    这是流式输出丝滑的关键：创建思考卡片时不阻塞 LLM 流式处理。
    """
    ok, mid = await _post_interactive_message_async(
        config,
        receive_id=chat_id,
        card_json=card_json,
        reply_to_message_id=reply_to_message_id,
        reply_in_thread=reply_in_thread,
        timeout=timeout,
    )
    if ok and mid:
        return mid
    return None


def _patch_interactive_thinking_message(
    config: FeishuConfig, message_id: str, card_json: str
) -> bool:
    """PATCH 更新已有交互卡片内容。"""
    try:
        import lark_oapi as lark
        from lark_oapi.api.im.v1 import PatchMessageRequest, PatchMessageRequestBody

        client = lark.Client.builder().app_id(config.app_id).app_secret(config.app_secret).build()
        body = PatchMessageRequestBody.builder().content(card_json).build()
        request = PatchMessageRequest.builder().message_id(message_id).request_body(body).build()
        response = client.im.v1.message.patch(request)
        if response.success():
            return True
        _logger.warning("更新思考消息失败: %s %s", response.code, response.msg)
    except ImportError as e:
        _logger.debug("lark-oapi未安装，跳过思考消息更新: %s", e)
    except Exception as e:
        _logger.debug("更新思考消息异常: %s", e)
    return False


async def _patch_interactive_thinking_message_async(
    config: FeishuConfig,
    message_id: str,
    card_json: str,
    timeout: float = 10.0,
) -> bool:
    """异步 PATCH 更新已有交互卡片内容。

    使用 asyncio.to_thread 包装同步 SDK 调用，避免阻塞事件循环。
    这是流式输出丝滑的关键：PATCH 更新飞书思考卡片时，
    不会阻塞 LLM 流式处理，用户感知卡片实时更新。

    Args:
        config: 飞书配置
        message_id: 要更新的消息 ID
        card_json: 新卡片内容 JSON
        timeout: 超时秒数（默认 10 秒，比发送更短）

    Returns:
        bool: 更新成功返回 True
    """
    from miniagent.assistant.feishu.im_send import patch_im_message_async

    ok, err = await patch_im_message_async(
        config,
        message_id=message_id,
        content_json=card_json,
        timeout=timeout,
    )
    if not ok:
        _logger.warning("更新思考消息失败: %s", err or "unknown")
        return False
    return True


def _update_thinking_round(st: Any, markdown: str, *, new_round: bool) -> None:
    """更新轮次正文，并在新轮次时保留旧工具段前的 LLM 正文。"""
    tool_marker = "\n\n**工具**"
    existing = getattr(st, "feishu_stream_accumulated", "") or ""
    tool_section = ""
    if tool_marker in existing and getattr(st, "feishu_tool_section_started", False):
        tool_section = existing[existing.index(tool_marker) :]
    round_separator = new_round and bool(tool_section)
    if new_round:
        if round_separator:
            llm_only = existing[: existing.index(tool_marker)].rstrip()
            st.feishu_stream_accumulated = (llm_only + "\n\n---\n\n") if llm_only else "\n\n---\n\n"
        else:
            st.feishu_stream_accumulated = ""
        st.feishu_last_patch_monotonic = 0.0
        st.feishu_last_patched_char_len = -1
        st.feishu_patch_budget = FEISHU_THINKING_PATCH_BUDGET
        st.feishu_tool_section_started = False
        st.feishu_stream_llm_len = 0
        st.feishu_pending_header = ""
        _reset_feishu_thinking_cache(st)
    if round_separator:
        st.feishu_stream_accumulated += (markdown or "") + tool_section
    else:
        pending_header = getattr(st, "feishu_pending_header", "") or ""
        if pending_header:
            st.feishu_stream_accumulated = f"**{pending_header}**\n\n{markdown or ''}"
            st.feishu_pending_header = ""
        else:
            st.feishu_stream_accumulated = markdown or ""
    st.feishu_stream_llm_len = len(markdown or "")


def _flush_pending_thinking_tools(st: Any) -> None:
    """把 LLM 正文前缓存的工具行合并进卡片正文。"""
    pending = getattr(st, "feishu_pending_tool_lines", None)
    if not pending:
        return
    st.feishu_stream_accumulated += "".join(pending)
    st.feishu_pending_tool_lines = []
    st.feishu_tool_section_started = True


async def _create_thinking_card(
    config: FeishuConfig, chat_id: str, markdown: str, card_json: str, st: Any
) -> bool:
    """缺少思考卡时创建首卡；返回是否已处理创建路径。"""
    import time

    if st.feishu_thinking_message_id:
        return False
    message_id = await _create_interactive_thinking_message_async(
        config,
        chat_id,
        card_json,
        reply_to_message_id=getattr(st, "feishu_reply_to_message_id", None),
        reply_in_thread=bool(getattr(st, "feishu_reply_in_thread", False)),
    )
    if message_id:
        st.feishu_thinking_message_id = message_id
        st.feishu_last_patch_monotonic = time.monotonic()
        st.feishu_last_patched_char_len = len(markdown)
        st.feishu_last_sent_card_json = card_json
    return True


async def _patch_thinking_card(
    config: FeishuConfig, markdown: str, card_json: str, st: Any
) -> None:
    """按时间/字符/结构化内容阈值节流更新已有思考卡。"""
    import time

    now = time.monotonic()
    need_patch = (
        now - st.feishu_last_patch_monotonic >= FEISHU_THINKING_PATCH_MIN_INTERVAL_S
        or len(markdown) - st.feishu_last_patched_char_len >= FEISHU_THINKING_PATCH_MIN_CHAR_DELTA
        or (FEISHU_PATCH_IMPORTANT_CONTENT_IMMEDIATE and _card_rendering.is_important_content_for_immediate_patch(markdown))
    )
    st.feishu_patch_budget = _card_rendering.adjust_patch_budget_dynamically(
        len(st.feishu_stream_accumulated), st.feishu_patch_budget
    )
    changed = card_json != getattr(st, "feishu_last_sent_card_json", None)
    if not changed or not need_patch or st.feishu_patch_budget <= 0:
        return
    if await _patch_interactive_thinking_message_async(
        config, st.feishu_thinking_message_id, card_json
    ):
        st.feishu_patch_budget -= 1
        st.feishu_last_patch_monotonic = now
        st.feishu_last_patched_char_len = len(markdown)
        st.feishu_last_sent_card_json = card_json


async def push_feishu_thinking_stream(
    config: FeishuConfig,
    chat_id: str,
    markdown: str,
    template: str,
    st: Any,
    *,
    new_round: bool,
    confirmation_engine: Any | None = None,
) -> None:
    """ReAct 单轮 LLM 流式思考：同一会话只保留一条卡片，用 PATCH 节流更新（避免每条 chunk 新建消息）。"""
    chat_id = _card_rendering.normalize_im_receive_chat_id(chat_id)
    if not chat_id:
        return
    _update_thinking_round(st, markdown, new_round=new_round)
    _flush_pending_thinking_tools(st)

    _sk = getattr(st, "feishu_session_key", None) or None
    card_json = _thinking_card_json_cached(
        st,
        st.feishu_stream_accumulated,
        template,
        _sk,
        confirmation_engine,
    )

    if await _create_thinking_card(config, chat_id, markdown, card_json, st):
        return
    await _patch_thinking_card(config, markdown, card_json, st)


async def finalize_feishu_thinking_stream(
    config: FeishuConfig,
    chat_id: str,
    template: str,
    st: Any,
    *,
    confirmation_engine: Any | None = None,
) -> None:
    """一轮 LLM 流结束或非合并的非流式块前：PATCH 首张卡片为正文第一段；超长则追加多张「思考续页」卡片。"""
    chat_id = _card_rendering.normalize_im_receive_chat_id(chat_id)
    mid = getattr(st, "feishu_thinking_message_id", None)
    acc = getattr(st, "feishu_stream_accumulated", "") or ""
    if not chat_id or not mid:
        # 无卡片可 finalize，仍清理状态
        _reset_feishu_thinking_state(st)
        return
    if not acc.strip():
        # 无累积内容，直接清理状态
        _reset_feishu_thinking_state(st)
        return
    prep = _card_rendering.prepare_thinking_body_for_card(acc, apply_cap=False)
    chunks = _card_rendering.chunk_card_markdown(prep, already_normalized=True)
    if not chunks:
        return
    nch = len(chunks)
    first_body = _card_rendering.prepare_card_markdown(chunks[0], normalize=False)
    _sk = getattr(st, "feishu_session_key", None) or None
    card_json = json.dumps(
        _thinking_interactive_card_dict(
            first_body,
            template,
            session_key=_sk,
            confirmation_engine=confirmation_engine,
        ),
        ensure_ascii=False,
    )
    # ✅ 使用异步版本：PATCH 收尾时不阻塞事件循环
    if card_json == getattr(st, "feishu_last_sent_card_json", None):
        patched = True
    else:
        patched = await _patch_interactive_thinking_message_async(config, mid, card_json)
        if patched:
            st.feishu_last_sent_card_json = card_json
    if not patched:
        _logger.warning("finalize 思考 PATCH 失败 message_id=%s", mid)
    if nch > 1:
        r_mid = getattr(st, "feishu_reply_to_message_id", None)
        r_thr = bool(getattr(st, "feishu_reply_in_thread", False))
        for j in range(1, nch):
            body = _card_rendering.prepare_card_markdown(chunks[j], normalize=False)
            title = f"💭 思考中 ({j + 1}/{nch})"
            card = build_interactive_card(title, body, template)
            req_json = json.dumps(card, ensure_ascii=False)
            # ✅ 使用异步版本：发送续页时不阻塞事件循环
            ok, _ = await _post_interactive_message_async(
                config,
                receive_id=chat_id,
                card_json=req_json,
                reply_to_message_id=r_mid,
                reply_in_thread=r_thr,
            )
            if not ok:
                _logger.warning("思考续页发送失败 (%s/%s)", j + 1, nch)
                break
    if patched:
        _reset_feishu_thinking_state(st)


async def append_feishu_thinking_same_card(
    config: FeishuConfig,
    chat_id: str,
    tool_line: str,
    template: str,
    st: Any,
    *,
    confirmation_engine: Any | None = None,
) -> None:
    """同轮工具意图：追加到当前思考卡片的 lark_md 正文并 PATCH（不新建消息、不计入流式 PATCH 预算）。"""
    chat_id = _card_rendering.normalize_im_receive_chat_id(chat_id)
    line = (tool_line or "").strip()
    if not chat_id or not line:
        return

    acc = (getattr(st, "feishu_stream_accumulated", None) or "") or ""
    mid = getattr(st, "feishu_thinking_message_id", None)
    section_started = bool(getattr(st, "feishu_tool_section_started", False))
    flat = line.replace("\n", " ").strip()
    if not section_started:
        addition = f"\n\n**工具**\n\n- {flat}"
        st.feishu_tool_section_started = True
    else:
        addition = f"\n- {flat}"

    acc2 = acc + addition
    st.feishu_stream_accumulated = acc2
    _sk = getattr(st, "feishu_session_key", None) or None
    card_json = _thinking_card_json_cached(
        st,
        acc2,
        template,
        _sk,
        confirmation_engine,
    )

    if mid:
        # ✅ 使用异步版本：追加工具后 PATCH 不阻塞事件循环
        if card_json == getattr(st, "feishu_last_sent_card_json", None):
            return
        if not await _patch_interactive_thinking_message_async(config, mid, card_json):
            _logger.warning(
                "飞书思考卡片追加工具后 PATCH 失败 message_id=%s（正文已累积，客户端可能未刷新）",
                mid,
            )
        else:
            st.feishu_last_sent_card_json = card_json
        return

    # 尚无卡片：缓冲工具行，等待 LLM 流式创建卡片时一并写入
    pending = getattr(st, "feishu_pending_tool_lines", None)
    if pending is None:
        st.feishu_pending_tool_lines = [addition]
    else:
        pending.append(addition)


async def _send_thinking(
    config: FeishuConfig,
    chat_id: str,
    thinking: str,
    template: str = "gray",
    *,
    reply_to_message_id: str | None = None,
    reply_in_thread: bool = False,
) -> None:
    """通过飞书 API 发送思考过程（交互式卡片）。

    默认与流式思考合并为同卡；本函数仅在 ``merge_tools`` 关闭或非同轮 header 等场景下发送**独立**短卡片。
    """
    chat_id = _card_rendering.normalize_im_receive_chat_id(chat_id)
    if not chat_id:
        _logger.debug("跳过发送思考：空的 chat_id")
        return

    try:
        cleaned = _card_rendering.prepare_thinking_markdown(thinking)
        card_json = json.dumps(
            _thinking_interactive_card_dict(cleaned, template), ensure_ascii=False
        )
        ok, _ = await _post_interactive_message_async(
            config,
            receive_id=chat_id,
            card_json=card_json,
            reply_to_message_id=reply_to_message_id,
            reply_in_thread=reply_in_thread,
        )
        if not ok:
            _logger.warning("发送思考失败（interactive）")

    except Exception as e:
        _logger.debug("发送思考异常: %s", e)


async def send_reflection_card(
    config: FeishuConfig,
    chat_id: str,
    reflection: Any,
    *,
    reply_to_message_id: str | None = None,
    thread_id: str | None = None,
) -> None:
    """发送质量评估独立卡片。

    Args:
        config: 飞书配置
        chat_id: 聊天 ID
        reflection: ReflectionResult 对象
        reply_to_message_id: 回复的目标消息 ID
        thread_id: 话题 ID
    """
    chat_id = _card_rendering.normalize_im_receive_chat_id(chat_id)
    if not chat_id:
        return

    status = "质量评估通过" if getattr(reflection, "acceptable", True) else "质量评估需改进"
    template = "gray" if reflection.acceptable else "warning"
    score = getattr(reflection, "quality_score", 0)
    issues = getattr(reflection, "issues", []) or []
    suggestions = getattr(reflection, "suggestions", []) or []

    lines: list[str] = [
        "### 质量评估结果",
        f"- **状态**：{status}",
        f"- **评分**：{score:.1f}/1.0",
    ]
    if issues:
        lines.append("")
        lines.append("### 发现问题")
        for issue in issues[:5]:
            lines.append(f"- {issue}")
    if suggestions:
        lines.append("")
        lines.append("### 改进建议")
        for s in suggestions[:5]:
            lines.append(f"- {s}")

    body = "\n".join(lines)
    cleaned = _card_rendering.prepare_thinking_markdown(body)
    # 使用 "🤖 Mini Agent" 卡片头，与 .help 命令输出格式一致
    card_json = json.dumps(
        build_interactive_card("🤖 Mini Agent", cleaned, template), ensure_ascii=False
    )

    ok, _ = await _post_interactive_message_async(
        config,
        receive_id=chat_id,
        card_json=card_json,
        reply_to_message_id=reply_to_message_id,
        reply_in_thread=bool(thread_id),
    )
    if not ok:
        _logger.warning("发送质量评估卡片失败")


__all__ = [
    "append_feishu_thinking_same_card",
    "finalize_feishu_thinking_stream",
    "push_feishu_thinking_stream",
    "send_reflection_card",
]
