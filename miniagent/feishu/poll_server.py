"""Mini Agent Python — 飞书 WebSocket 长轮询

使用飞书 SDK WSClient 长轮询模式接收事件推送。

核心机制（对齐 OpenClaw）：
- 实例连接所有权：每个 ``FeishuRuntime`` 只维护一个活动 SDK 客户端
- 内存+磁盘双重去重：防止重复处理同一消息（已拆分至 feishu_dedup.py）
- 聊天室顺序队列：防止并发导致上下文混乱
- 消息防抖：合并同一发送者短时内的连续消息
- 优雅关闭：SIGINT/SIGTERM 信号处理

适用场景：
- 无需公网 IP，适合家庭网络或内网部署
- 飞书开放平台的企业自建应用

媒体落盘后是否自动跑 Agent、静默回复等开关见 ``docs/ENGINEERING.md`` §1 表格与 ``docs/FEISHU.md``。

**与消息队列的边界**：本模块在事件回调中组包用户文本（及可选媒体路径）后，应通过路由层投递到
``MessageQueueManager``，由队列保证同聊天室与 CLI 侧约定的顺序/抢占语义；本文件不直接替代
``miniagent.infrastructure.message_queue``。

**已拆分模块**：
- feishu/feishu_dedup.py: 消息去重逻辑（内存+磁盘）— 本模块已导入使用
- feishu/ws_client.py: WebSocket 连接管理
- feishu/ws_health.py: WebSocket 健康监督
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from miniagent.core.constants import (
    FEISHU_PATCH_BUDGET,
    FEISHU_PATCH_CHAR_DELTA,
    FEISHU_PATCH_IMPORTANT_CONTENT_IMMEDIATE,
    FEISHU_PATCH_INTERVAL_S,
)
from miniagent.feishu.types import FeishuConfig, FeishuInboundText
from miniagent.infrastructure.json_config import get_config
from miniagent.infrastructure.logger import get_logger
from miniagent.types.error_prefix import SUCCESS_PREFIX

_logger = get_logger(__name__)

# ── 性能优化：预编译正则表达式 ──
# 将常用的正则表达式预编译为模块级常量，避免每次调用都重新编译
_RE_LONE_ASTERISK = re.compile(r"(?<!\*)\*(?!\*)")
_RE_TRIPLE_NEWLINE = re.compile(r"\n{3,}")
_RE_BR_TAG = re.compile(r"<br\s*/?>", re.IGNORECASE)
_RE_FENCE_LINE = re.compile(r"^(`{3,})(.*)$")
_RE_ATX_HEADING = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
_RE_HORIZONTAL_RULE = re.compile(r"(?m)^[ \t]*(?:---+|\*{3,}|_{3,})[ \t]*$")
_RE_CODE_FENCE = re.compile(r"```[^\n]*\n([\s\S]*?)```")
_RE_INLINE_CODE = re.compile(r"`([^`]+)`")
_RE_BOLD_STAR = re.compile(r"\*\*([^*]+)\*\*")
_RE_BOLD_UNDERSCORE = re.compile(r"__([^_]+)__")

# --- 出站：reply 参数、interactive/text（经 im_send）---
# 入站文本 handler：单参数 ``FeishuInboundText``，返回回复正文。
FeishuTextMessageHandler = Callable[[FeishuInboundText], Awaitable[str]]


def _resolve_feishu_confirmation_channel(
    runtime_state: FeishuPollState,
    chat_id: str,
    sender_id: str,
    chat_type: str | None = None,
) -> Any | None:
    """按飞书入站上下文解析 per-session ConfirmationChannel。"""
    eng = runtime_state.confirmation_engine
    if eng is None:
        return None
    session_key: str | None = None
    if runtime_state.channel_router is not None:
        try:
            session_key = runtime_state.channel_router.resolve_feishu_message(
                chat_id, sender_id, chat_type or "group"
            )
        except Exception as e:
            _logger.debug("resolve_feishu_message 失败: %s", e)
    if session_key and hasattr(eng, "get_confirmation_channel"):
        return eng.get_confirmation_channel(session_key)
    return getattr(eng, "confirmation_channel", None)


def feishu_outbound_reply_params(
    trigger_message_id: str | None,
    thread_id: str | None = None,
) -> tuple[str | None, bool]:
    """是否使用飞书「回复消息」API（``im/v1/messages/:message_id/reply``）。

    环境变量：
    - ``MINIAGENT_FEISHU_REPLY_TARGET``：``reply``（默认，回复入站消息）或 ``create``（会话内新消息）；其它值视为 ``create``。
    - ``MINIAGENT_FEISHU_REPLY_IN_THREAD``：显式 ``1``/``true`` 等为真；``0``/``false`` 等为假；**未设置**且入站
      ``thread_id`` 非空时，在 ``reply`` 模式下默认 ``reply_in_thread=True``。

    Returns:
        ``(reply_parent_message_id_or_None, reply_in_thread)``
    """
    mode = str(get_config("feishu.reply_target", "reply")).lower()
    if mode != "reply":
        return None, False
    mid = (trigger_message_id or "").strip()
    if not mid:
        return None, False
    thr_cfg = get_config("feishu.reply_in_thread", None)
    if thr_cfg is None:
        thr = bool((thread_id or "").strip())
    else:
        thr = bool(thr_cfg)
    return mid, thr


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


class FeishuMediaHandler(Protocol):
    """file/image 入站异步处理：成功或已落盘应返回非「⚠️」前缀字符串；失败返回 ``⚠️`` 前缀以便不入磁盘去重。"""

    async def __call__(
        self,
        config: FeishuConfig,
        message_id: str,
        chat_id: str,
        sender_id: str,
        chat_type: str,
        msg_type: str,
        file_key: str,
        suggested_name: str,
        resource_type: str,
        thread_id: str | None = None,
    ) -> str | None: ...


def _parse_feishu_media_payload(msg_type: str, content_str: str) -> tuple[str, str, str] | None:
    """解析 file/image 消息的 file_key 与建议文件名。返回 (resource_type, file_key, suggested_name)。"""
    try:
        d = json.loads(content_str or "{}")
    except (json.JSONDecodeError, TypeError):
        return None
    if msg_type == "file":
        fk = d.get("file_key")
        name = d.get("file_name") or d.get("name") or "download"
        if not fk:
            return None
        return ("file", str(fk), str(name))
    if msg_type == "image":
        ik = d.get("image_key")
        if not ik:
            return None
        return ("image", str(ik), "image")
    return None


def _extract_post_media_items(content_str: str) -> list[tuple[str, str, str]]:
    """从 post 富文本 JSON 中收集 (resource_type, file_key_or_image_key, suggested_name)。

    性能优化：迭代替代递归，限制遍历深度（防止恶意深层 JSON）。
    """
    out: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    max_depth = 10  # 限制遍历深度

    try:
        root = json.loads(content_str or "{}")
    except (json.JSONDecodeError, TypeError):
        return []

    # 性能优化：迭代遍历替代递归
    stack: list[tuple[Any, int]] = [(root, 0)]  # (node, depth)
    while stack:
        node, depth = stack.pop()
        if depth > max_depth:
            continue  # 超过深度限制，跳过
        if isinstance(node, dict):
            tag = node.get("tag")
            if tag == "img":
                ik = node.get("image_key") or node.get("image_token")
                if ik and ("image", str(ik)) not in seen:
                    seen.add(("image", str(ik)))
                    out.append(("image", str(ik), "image"))
            elif tag == "media":
                fk = node.get("file_key")
                if fk and ("file", str(fk)) not in seen:
                    seen.add(("file", str(fk)))
                    nm = node.get("file_name") or node.get("name") or "download"
                    out.append(("file", str(fk), str(nm)))
            # 将子节点加入栈（反向顺序保持深度优先顺序）
            for v in reversed(list(node.values())):
                stack.append((v, depth + 1))
        elif isinstance(node, list):
            # 反向顺序保持原始遍历顺序
            for x in reversed(node):
                stack.append((x, depth + 1))

    return out


class FeishuPollState:
    """Connection state owned by one ``FeishuRuntime`` instance."""

    def __init__(self) -> None:
        from miniagent.feishu.cards.dedupe import CardActionDeduplicator
        from miniagent.feishu.feishu_dedup import FeishuDeduplicator
        from miniagent.feishu.message_debounce import FeishuMessageDebouncer
        from miniagent.feishu.ws_health import FeishuWsHealthState

        self.client: Any | None = None
        self.app_id: str | None = None
        self.shutdown_event: asyncio.Event | None = None
        self.debouncer = FeishuMessageDebouncer()
        self.deduplicator = FeishuDeduplicator()
        self.card_actions = CardActionDeduplicator()
        self.ws_health = FeishuWsHealthState()
        self.confirmation_engine: Any | None = None
        self.channel_router: Any | None = None
        self.callback_tasks: set[asyncio.Task[Any]] = set()

    def bind_confirmation(self, engine: Any, channel_router: Any | None) -> None:
        """Bind confirmation routing dependencies to this Feishu runtime."""
        self.confirmation_engine = engine
        self.channel_router = channel_router

    def request_shutdown(self) -> None:
        """Signal the active supervised session, if any, to stop."""
        if self.shutdown_event is not None:
            self.shutdown_event.set()

    def spawn_callback_task(self, awaitable: Awaitable[Any]) -> asyncio.Task[Any]:
        """Track async work bridged from a synchronous SDK callback."""
        try:
            task = asyncio.create_task(awaitable)
        except RuntimeError:
            if asyncio.iscoroutine(awaitable):
                awaitable.close()
            raise
        self.callback_tasks.add(task)

        def _done(completed: asyncio.Task[Any]) -> None:
            self.callback_tasks.discard(completed)
            if completed.cancelled():
                return
            error = completed.exception()
            if error is not None:
                _logger.error("飞书回调任务异常: %s", error, exc_info=error)

        task.add_done_callback(_done)
        return task

    async def reset(self) -> None:
        """Disconnect the active SDK client and clear pending debounce tasks."""
        callback_tasks = [task for task in self.callback_tasks if not task.done()]
        for task in callback_tasks:
            task.cancel()
        if callback_tasks:
            await asyncio.gather(*callback_tasks, return_exceptions=True)
        self.callback_tasks.clear()
        await self.debouncer.reset()
        await self.deduplicator.close()
        client = self.client
        self.client = None
        self.app_id = None
        self.shutdown_event = None
        if client is not None:
            try:
                await client._disconnect()
            except Exception as error:
                _logger.debug("FeishuPollState.reset: %s", error)


def _feishu_media_reply_indicates_failure(reply: str | None) -> bool:
    """media_handler 用「⚠️」前缀表示不可落盘的失败类回复。"""
    if not reply:
        return False
    return reply.lstrip().startswith("\u26a0\ufe0f")


# ─── 长轮询入口：WSClient、事件回调、handler 内投递 message_queue ───
# 与 ``# ─── 消息队列 ───`` 注释呼应：此处只负责连接与解析，顺序语义由传入的 ``message_queue`` 保证。


async def start_feishu_poll_server(
    config: FeishuConfig,
    message_handler: FeishuTextMessageHandler,
    *,
    runtime_state: FeishuPollState,
    message_queue: Any,
    media_handler: FeishuMediaHandler | None = None,
) -> None:
    """启动飞书 WebSocket 长轮询模式。

    建立与飞书服务器的 WebSocket 连接，
    持续接收事件推送并分发给消息处理器。

    Args:
        config: 飞书应用配置
        message_handler: 消息处理函数 ``FeishuInboundText`` => 回复正文
        message_queue: 本进程使用的消息队列管理器（与 CLI 共用）
        media_handler: 可选；处理 file/image 入站（见 ``_create_feishu_handler`` 返回的第二个回调）
    """
    mq = message_queue
    try_begin_processing = runtime_state.deduplicator.try_begin_processing
    release_processing = runtime_state.deduplicator.release_processing
    abandon_processing_claim = runtime_state.deduplicator.abandon_processing_claim
    # #region agent log
    try:
        from miniagent.infrastructure.debug_ndjson import agent_debug_log

        agent_debug_log(
            hypothesis_id="E",
            location="poll_server.py:start_feishu_poll_server",
            message="poll_server_entry",
            data={"app_id_len": len((config.app_id or "").strip())},
        )
    except Exception as e:
        _logger.debug("Agent debug log 记录失败（非关键）: %s", e)
    # #endregion

    # 任何实例残留连接一律关闭后重建，避免外层重连误判为断线空转。
    if runtime_state.client is not None:
        if runtime_state.app_id != config.app_id:
            _logger.info("存在不同 appId 的 WSClient (%s)，先关闭", runtime_state.app_id)
        else:
            _logger.warning("检测到残留 WebSocket 客户端（与当前 appId 相同），将关闭后重建")
        await runtime_state.reset()

    # 加载 SDK
    try:
        from lark_oapi.api.im.v1.model.p2_im_message_receive_v1 import P2ImMessageReceiveV1
        from lark_oapi.core.enum import LogLevel
        from lark_oapi.event.dispatcher_handler import EventDispatcherHandler
    except ImportError as e:
        # #region agent log
        try:
            from miniagent.infrastructure.debug_ndjson import agent_debug_log

            agent_debug_log(
                hypothesis_id="E",
                location="poll_server.py:start_feishu_poll_server",
                message="lark_oapi_import_failed",
                data={"exc_type": "ImportError", "exc_msg": str(e)[:300]},
            )
        except Exception as e:
            _logger.debug("Agent debug log 记录失败（导入错误）: %s", e)
        # #endregion
        _logger.error("请安装 lark-oapi: pip install lark-oapi (%s)", e)
        raise

    # 同步回调（SDK 要求 sync），内部通过 asyncio.create_task 调度 async 逻辑
    def on_message_receive(event: P2ImMessageReceiveV1) -> None:
        """处理 im.message.receive_v1 事件。"""
        try:
            runtime_state.ws_health.touch_inbound()
            message = event.event.message
            if not message:
                return

            message_id = message.message_id or ""
            if not message_id:
                _logger.warning("收到无 message_id 的事件，跳过")
                return

            # 去重检查
            if not try_begin_processing(message_id):
                _logger.debug("跳过重复消息: %s", message_id)
                return

            # 过期消息拦截：基于 message.create_time（秒级 Unix 时间戳）
            _raw_create_time = getattr(message, "create_time", None) or 0
            try:
                msg_create_time = int(_raw_create_time) if _raw_create_time else 0
            except (ValueError, TypeError):
                msg_create_time = 0
            if msg_create_time > 0:
                _msg_age = time.time() - msg_create_time
                _max_age = get_config("feishu.max_message_age", 600)
                if _msg_age > _max_age:
                    _logger.info(
                        "跳过过期消息: message_id=%s, age=%.0fs > max=%.0fs",
                        message_id,
                        _msg_age,
                        _max_age,
                    )
                    release_processing(message_id)
                    return

            chat_id = message.chat_id or ""
            sender = event.event.sender
            sender_id = (sender.sender_id.open_id or "") if sender and sender.sender_id else ""
            msg_type = message.message_type or ""
            chat_type = getattr(event.event.message, "chat_type", "group") or "group"

            content_str = message.content or ""

            if msg_type in ("text", "interactive"):
                from miniagent.feishu.cards.extract import inbound_text_from_message

                text = ""
                if msg_type == "interactive" and get_config("feishu.card_extract_inbound", True):
                    text = inbound_text_from_message(msg_type, content_str) or ""
                if not text and msg_type == "text":
                    try:
                        parsed = json.loads(content_str)
                        text = parsed.get("text", "")
                    except (json.JSONDecodeError, TypeError):
                        text = content_str

                if not text.strip():
                    release_processing(message_id)
                    return

                _logger.debug("收到消息 [%s] %s: %s", chat_id, sender_id, text)

                root_id = (message.root_id or "").strip() or None
                parent_id = (message.parent_id or "").strip() or None
                thread_id = (message.thread_id or "").strip() or None
                inbound = FeishuInboundText(
                    text=text,
                    chat_id=chat_id,
                    sender_id=sender_id,
                    chat_type=chat_type or "group",
                    message_id=message_id,
                    root_id=root_id,
                    parent_id=parent_id,
                    thread_id=thread_id,
                    create_time=msg_create_time,
                )

                def _make_text_handle(
                    merged: FeishuInboundText, claim_ids: list[str]
                ) -> Callable[[], Awaitable[None]]:
                    async def _handle() -> None:
                        finalized = False
                        try:
                            reply = await message_handler(merged)
                            if reply:
                                r_mid, r_thr = feishu_outbound_reply_params(
                                    merged.message_id, merged.thread_id
                                )
                                await _send_reply(
                                    config,
                                    chat_id,
                                    reply,
                                    reply_to_message_id=r_mid,
                                    reply_in_thread=r_thr,
                                )
                                _logger.debug("已回复 [%s]", chat_id)
                            finalized = True
                        except Exception as e:
                            _logger.error("处理消息失败: %s", e)
                        finally:
                            if finalized:
                                for mid in claim_ids:
                                    release_processing(mid)
                            else:
                                for mid in claim_ids:
                                    abandon_processing_claim(mid)

                    return _handle

                from miniagent.feishu.message_debounce import (
                    feishu_message_debounce_ms,
                )

                debouncer = runtime_state.debouncer
                debounce_ms = feishu_message_debounce_ms()

                async def _dispatch_text(inb: FeishuInboundText, claim_ids: list[str]) -> None:
                    runtime_state.spawn_callback_task(
                        mq.dispatch(inb.chat_id, _make_text_handle(inb, claim_ids)())
                    )

                async def _schedule_debounced(inb: FeishuInboundText) -> None:
                    await debouncer.schedule(
                        inb,
                        debounce_ms=debounce_ms,
                        on_flush=_dispatch_text,
                    )

                # 命令走控制面：不得与 Agent 同锁排队，否则卡死时无法在飞书侧下发 `/abort` 等。
                if text.lstrip().startswith("/"):
                    runtime_state.spawn_callback_task(
                        mq.dispatch(chat_id, _make_text_handle(inbound, [message_id])())
                    )
                else:
                    # 需求澄清追问拦截：普通消息自动注入为回答
                    _cc = _resolve_feishu_confirmation_channel(
                        runtime_state, chat_id, sender_id, chat_type or "group"
                    )
                    if _cc and _cc.has_pending:
                        from miniagent.types.confirmation import (
                            ConfirmationResult,
                            ConfirmationStage,
                        )

                        if _cc.pending.stage == ConfirmationStage.CLARIFICATION:
                            _logger.info(
                                "飞书澄清拦截: chat_id=%s, text=%s", chat_id[:12], text[:60]
                            )
                            _cc.respond(ConfirmationResult.clarification_reply(text))
                            release_processing(message_id)
                            _logger.info("飞书澄清已响应: confirmation_channel.respond() 已调用")
                        else:
                            _logger.debug(
                                "飞书拦截: 有待确认请求但阶段为 %s，非 CLARIFICATION，走消息队列",
                                getattr(_cc.pending.stage, "value", _cc.pending.stage),
                            )
                            runtime_state.spawn_callback_task(_schedule_debounced(inbound))
                    else:
                        _logger.debug("飞书拦截: 无待确认请求，走消息队列")
                        runtime_state.spawn_callback_task(_schedule_debounced(inbound))
            elif msg_type in ("file", "image") and media_handler:
                parsed_media = _parse_feishu_media_payload(msg_type, content_str)
                if not parsed_media:
                    release_processing(message_id)
                    return
                res_type, file_key, suggested_name = parsed_media
                thread_id_media = (message.thread_id or "").strip()

                async def _handle_media():
                    finalized = False
                    try:
                        reply = await media_handler(
                            config,
                            message_id,
                            chat_id,
                            sender_id,
                            chat_type,
                            msg_type,
                            file_key,
                            suggested_name,
                            res_type,
                            thread_id_media or None,
                        )
                        if _feishu_media_reply_indicates_failure(reply):
                            finalized = False
                        else:
                            silent = bool(get_config("feishu.media.silent_reply", False))
                            if reply and not silent:
                                r_mid, r_thr = feishu_outbound_reply_params(
                                    message_id, thread_id_media or None
                                )
                                await _send_reply(
                                    config,
                                    chat_id,
                                    reply,
                                    reply_to_message_id=r_mid,
                                    reply_in_thread=r_thr,
                                )
                            finalized = True
                    except Exception as e:
                        _logger.error("处理飞书媒体失败: %s", e)
                    finally:
                        if finalized:
                            release_processing(message_id)
                        else:
                            abandon_processing_claim(message_id)

                runtime_state.spawn_callback_task(mq.dispatch(chat_id, _handle_media()))
            elif msg_type == "post" and media_handler:
                post_items = _extract_post_media_items(content_str)
                if not post_items:
                    release_processing(message_id)
                    return
                thread_id_post = (message.thread_id or "").strip()

                async def _handle_post_media():
                    finalized = False
                    silent = bool(get_config("feishu.media.silent_reply", False))
                    combined: list[str] = []
                    try:
                        for res_type, fk, suggested in post_items:
                            reply = await media_handler(
                                config,
                                message_id,
                                chat_id,
                                sender_id,
                                chat_type,
                                "post",
                                fk,
                                suggested,
                                res_type,
                                thread_id_post or None,
                            )
                            if _feishu_media_reply_indicates_failure(reply):
                                finalized = False
                                return
                            if reply:
                                combined.append(reply)
                        if combined and not silent:
                            r_mid, r_thr = feishu_outbound_reply_params(
                                message_id, thread_id_post or None
                            )
                            await _send_reply(
                                config,
                                chat_id,
                                "\n".join(combined),
                                reply_to_message_id=r_mid,
                                reply_in_thread=r_thr,
                            )
                        finalized = True
                    except Exception as e:
                        _logger.error("处理飞书 post 媒体失败: %s", e)
                    finally:
                        if finalized:
                            release_processing(message_id)
                        else:
                            abandon_processing_claim(message_id)

                runtime_state.spawn_callback_task(mq.dispatch(chat_id, _handle_post_media()))
            else:
                release_processing(message_id)
                return

        except Exception as e:
            _logger.error("事件处理异常: %s", e)

    def _feishu_card_action_router_enabled() -> bool:
        """是否将卡片按钮事件经路由投递到消息队列（环境变量开关）。"""
        return bool(get_config("feishu.card_action_router", True))

    def _on_card_action_trigger(event: Any) -> Any:
        """同步回调：将卡片 ``action.value`` 中的文本投递给同一 ``message_handler``。"""
        from lark_oapi.event.callback.model.p2_card_action_trigger import (
            CallBackToast,
            P2CardActionTriggerResponse,
        )

        resp = P2CardActionTriggerResponse()
        bad = CallBackToast()
        bad.type = "error"
        bad.content = "Mini Agent：缺少 miniagent_text 或 chat_id（请在按钮 value 中提供）"
        resp.toast = bad

        try:
            runtime_state.ws_health.touch_inbound()
            ev = getattr(event, "event", None)
            if not ev:
                return resp
            act = getattr(ev, "action", None)
            ctx = getattr(ev, "context", None)
            op = getattr(ev, "operator", None)
            value = dict(getattr(act, "value", None) or {}) if act else {}
            from miniagent.feishu.cards.action_router import inbound_text_from_card_action_value

            text = (inbound_text_from_card_action_value(value) or "").strip()
            chat_id = str(value.get("chat_id") or "").strip()
            if not chat_id and ctx is not None:
                chat_id = str(getattr(ctx, "open_chat_id", None) or "").strip()
            sender_id = ""
            if op is not None:
                sender_id = str(getattr(op, "open_id", None) or "").strip()
            dedupe_key = str(value.get("dedupe_key") or "").strip()
            if dedupe_key:
                if runtime_state.card_actions.should_skip(dedupe_key):
                    ok = CallBackToast()
                    ok.type = "info"
                    ok.content = "已处理（重复操作已忽略）"
                    resp.toast = ok
                    return resp
            if not text or not chat_id:
                return resp

            # 拦截确认命令：直接响应确认通道，不经消息队列
            if text in ("/confirm", "/reject") or text.startswith("/adjust "):
                cc = _resolve_feishu_confirmation_channel(
                    runtime_state, chat_id, sender_id, "group"
                )
                if cc is not None and cc.has_pending:
                    from miniagent.types.confirmation import ConfirmationResult

                    if text == "/confirm":
                        cc.respond(ConfirmationResult.confirm())
                        ok = CallBackToast()
                        ok.type = "success"
                        ok.content = "✅ 已确认，继续执行"
                        resp.toast = ok
                        return resp
                    elif text == "/reject":
                        cc.respond(ConfirmationResult.reject())
                        ok = CallBackToast()
                        ok.type = "warning"
                        ok.content = "⚠️ 已拒绝，取消当前操作"
                        resp.toast = ok
                        return resp
                    else:
                        adjustment = text[len("/adjust ") :].strip()
                        if adjustment:
                            cc.respond(ConfirmationResult.adjust(adjustment))
                            ok = CallBackToast()
                            ok.type = "success"
                            ok.content = f"{SUCCESS_PREFIX} 已调整：{adjustment[:40]}{'…' if len(adjustment) > 40 else ''}"
                            resp.toast = ok
                            return resp
                # 无待确认请求或无引擎，继续走消息队列
            inbound = FeishuInboundText(
                text=text,
                chat_id=chat_id,
                sender_id=sender_id,
                chat_type=str(value.get("chat_type") or "group"),
                message_id=str(value.get("message_id") or "").strip(),
            )

            async def _card_job() -> None:
                try:
                    reply = await message_handler(inbound)
                    if reply:
                        cr_mid, cr_thr = feishu_outbound_reply_params(
                            inbound.message_id or None, inbound.thread_id
                        )
                        await _send_reply(
                            config,
                            chat_id,
                            reply,
                            reply_to_message_id=cr_mid,
                            reply_in_thread=cr_thr,
                        )
                except Exception as ex:
                    _logger.warning("卡片动作调度 Agent 失败: %s", ex)

            try:
                runtime_state.spawn_callback_task(mq.dispatch(chat_id, _card_job()))
            except RuntimeError:
                bad.content = "Mini Agent：无运行中的事件循环，无法调度"
                return resp
            ok = CallBackToast()
            ok.type = "info"
            ok.content = "已提交处理"
            resp.toast = ok
            return resp
        except Exception as e:
            _logger.warning("卡片动作入口异常: %s", e)
            bad.content = f"Mini Agent：{e}"
            return resp

    # 构建 EventDispatcherHandler
    encrypt_key = config.encrypt_key or ""
    verification_token = config.verification_token or ""
    _edb = EventDispatcherHandler.builder(
        encrypt_key, verification_token
    ).register_p2_im_message_receive_v1(on_message_receive)
    if _feishu_card_action_router_enabled():
        _edb = _edb.register_p2_card_action_trigger(_on_card_action_trigger)
    event_handler = _edb.build()

    # 启动 WebSocket 客户端
    from miniagent.feishu.ws_client import FeishuWsClient
    from miniagent.feishu.ws_health import supervise_feishu_ws_session

    ws_client: FeishuWsClient | None = None
    ping_task: asyncio.Task[Any] | None = None
    shutdown_event = asyncio.Event()
    runtime_state.shutdown_event = shutdown_event
    try:
        # ── 关键修复：lark-oapi SDK 在模块加载时捕获了 event loop，
        #    但 asyncio.run() 会创建全新 loop。如果不替换，
        #    SDK 的 _receive_message_loop() 会调度到错误的 loop 上，
        #    导致消息永远收不到、思考回调永远不触发。
        import lark_oapi.ws.client as _sdk_ws_mod

        _sdk_ws_mod.loop = asyncio.get_running_loop()

        ws_client = FeishuWsClient(
            app_id=config.app_id,
            app_secret=config.app_secret,
            event_handler=event_handler,
            # 避免 SDK 在 stdout 输出与全屏 CLI 冲突（备用屏乱序 / 分层）
            log_level=LogLevel.ERROR,
        )

        runtime_state.client = ws_client
        runtime_state.app_id = config.app_id

        _logger.info("WebSocket 长轮询模式已启动（无需公网 IP）")
        _logger.info("消息会通过 WebSocket 自动从飞书服务器拉取")

        # lark-oapi 的 start() 是同步方法，内部调用 loop.run_until_complete()
        # 在已运行的事件循环中无法使用。直接调用内部异步方法：
        await ws_client._connect()

        ping_task = asyncio.create_task(ws_client._ping_loop())

        try:
            await supervise_feishu_ws_session(
                ws_client,
                shutdown_event=shutdown_event,
                health_state=runtime_state.ws_health,
            )
            end_reason, _ = runtime_state.ws_health.last_session_end()
            _logger.info(
                "飞书 WebSocket 会话已结束（%s），将由外层退避后重连",
                end_reason or "unknown",
            )
        except (KeyboardInterrupt, asyncio.CancelledError):
            _logger.info("收到退出信号")
            shutdown_event.set()
            raise

    except Exception as e:
        _logger.error("WebSocket 启动失败: %s", e)
        raise
    finally:
        runtime_state.shutdown_event = None
        # 与 FeishuRuntime 循环开头的 reset 互补：保证异常/取消路径下 SDK 与实例状态一致。
        if ping_task is not None and not ping_task.done():
            ping_task.cancel()
            try:
                await ping_task
            except asyncio.CancelledError as e:
                _logger.debug("Ping任务取消（清理路径）: %s", e)
            except Exception as e:
                _logger.debug("Ping任务清理失败（非关键）: %s", e)
        # SDK 内部 _receive_message_loop 在 WebSocket 正常关闭时会抛出
        # ConnectionClosedOK，若该任务未被 await 则会产生 "Task exception was never
        # retrieved" 警告。此处显式消费该异常。
        try:
            from websockets.exceptions import ConnectionClosedOK
        except ImportError:
            ConnectionClosedOK = Exception  # type: ignore[misc, assignment]

        try:
            recv_task = getattr(ws_client, "receive_task", None)
            if recv_task is not None and not recv_task.done():
                recv_task.cancel()
                try:
                    await recv_task
                except (asyncio.CancelledError, ConnectionClosedOK) as e:
                    _logger.debug("接收任务取消或关闭（清理路径）: %s", e)
                except Exception as e:
                    _logger.debug("接收任务清理失败（非关键）: %s", e)
            elif recv_task is not None:
                # 已完成的任务显式读取结果，清除未检索异常
                try:
                    recv_task.result()
                except (ConnectionClosedOK, Exception) as e:
                    _logger.debug("读取接收任务结果失败（清理路径）: %s", e)
        except Exception as e:
            _logger.debug("清理接收任务失败（清理路径）: %s", e)
        await runtime_state.reset()


def _normalize_im_receive_chat_id(chat_id: str) -> str:
    """去掉内部路由前缀 ``feishu:``，得到 IM API 可用的 ``receive_id``（``receive_id_type=chat_id``）。"""
    c = (chat_id or "").strip()
    if c.startswith("feishu:"):
        return c[len("feishu:") :]
    return c


def _is_valid_im_receive_id(chat_id: str) -> bool:
    """群聊 oc_、单聊 ou_ 等可作为 ``receive_id``（与开放平台常见 ID 前缀一致）。"""
    c = (chat_id or "").strip()
    return bool(c) and (c.startswith("oc_") or c.startswith("ou_"))


# --- lark_md / GFM：规范化、宽表降级、卡片分片与 PATCH 节流（与 ThinkingDisplay 输出对齐）---
# 单条「思考」卡片：流式时用 PATCH 更新同一 message_id；飞书对单条消息可 PATCH 次数有限，须节流。
# 节流参数从JSON配置读取，默认值已优化为更流畅的流式体验（间隔更短、字符增量更小）

# 默认值：间隔 0.12s（比之前 0.35s 更快）、字符增量 30（比之前 450 更小）、预算 40（比之前 12 更多）
FEISHU_THINKING_PATCH_MIN_INTERVAL_S = float(FEISHU_PATCH_INTERVAL_S)
FEISHU_THINKING_PATCH_MIN_CHAR_DELTA = int(FEISHU_PATCH_CHAR_DELTA)
FEISHU_THINKING_PATCH_BUDGET = int(FEISHU_PATCH_BUDGET)


def _is_important_content_for_immediate_patch(text: str) -> bool:
    """判断内容是否重要，需要立即 PATCH（智能节流优化）。

    重要内容包括：
    - 代码块开始（```）：用户想立即看到代码
    - 表格结构：表格渲染需要完整结构
    - Markdown 标题：章节标题应该及时显示
    - 列表结构开始：列表需要及时显示

    Args:
        text: 待发送的文本内容

    Returns:
        bool: 需要立即 PATCH 返回 True
    """
    if not text:
        return False

    # 检查是否启用重要内容立即 PATCH
    enabled = FEISHU_PATCH_IMPORTANT_CONTENT_IMMEDIATE
    if not enabled:
        return False

    stripped = text.strip()

    # 代码块开始（未闭合的 ```）
    fence_count = text.count("```")
    if fence_count > 0 and fence_count % 2 == 1:
        return True

    # Markdown 标题
    if stripped.startswith("#") and len(stripped) > 1 and stripped[1] in (" ", "#"):
        return True

    # 表格行（包含 | 分隔符）
    if "|" in stripped and stripped.startswith("|"):
        return True

    # 列表开始
    if stripped.startswith("- ") or stripped.startswith("* ") or stripped.startswith("1. "):
        return True

    return False


def _adjust_patch_budget_dynamically(text_len: int, current_budget: int) -> int:
    """根据文本长度动态调整 PATCH 预算。

    长文本需要更多 PATCH 次数，动态增加预算避免中途停止更新。

    Args:
        text_len: 当前累积文本长度
        current_budget: 当前剩余预算

    Returns:
        int: 调整后的预算
    """
    # 长文本增加预算（先检查更长的条件）
    if text_len > 10000 and current_budget < FEISHU_THINKING_PATCH_BUDGET + 40:
        return FEISHU_THINKING_PATCH_BUDGET + 40
    if text_len > 5000 and current_budget < FEISHU_THINKING_PATCH_BUDGET + 20:
        return FEISHU_THINKING_PATCH_BUDGET + 20
    return current_budget


def feishu_card_body_max() -> int:
    """单张交互卡片 lark_md 正文上限（字符近似）。"""
    val = get_config("feishu.card.body_max_chars", 48000)
    return max(1000, int(val)) if val else 48_000


def feishu_card_thinking_max() -> int:
    """思考流卡片正文上限；未单独配置时与 body_max_chars 相同。"""
    val = get_config("feishu.card.thinking_max_chars", None)
    if val is not None:
        return max(1000, int(val))
    return feishu_card_body_max()


def _strip_unicode_replacement_chars(text: str) -> str:
    """去掉 U+FFFD，减少工具输出乱码时的占位符刷屏。"""
    return (text or "").replace("\ufffd", "")


def _neutralize_lone_asterisks_for_lark(text: str) -> str:
    """将不成对的 ASCII `*` 换成全角 `＊`，减轻 lark_md 误解析为斜体。

    代码围栏（`` ``` ``）内的 `*` 不受影响。
    """
    lines = (text or "").split("\n")
    in_fence = False
    result: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            result.append(line)
            continue
        if not in_fence:
            line = _RE_LONE_ASTERISK.sub("＊", line)  # 性能优化：预编译正则
        result.append(line)
    return "\n".join(result)


def _collapse_excessive_blank_lines(text: str) -> str:
    """将连续三个及以上换行压成双换行，避免卡片正文过长空白。"""
    return _RE_TRIPLE_NEWLINE.sub("\n\n", text or "")  # 性能优化：预编译正则


def _normalize_lark_md(text: str) -> str:
    """将常见 GFM / HTML 写法降级为飞书 ``lark_md`` 更易接受的正文。"""
    if not text:
        return ""
    t = text.replace("\ufeff", "").replace("\u200b", "").replace("\u200c", "")
    t = _strip_unicode_replacement_chars(t)
    t = _neutralize_lone_asterisks_for_lark(t)
    t = _RE_BR_TAG.sub("\n", t)  # 性能优化：预编译正则

    def _collapse_fence_line(line: str) -> str:
        """将过长反引号围栏起首统一为三个 ```，兼容飞书 lark_md。"""
        m = _RE_FENCE_LINE.match(line)  # 性能优化：预编译正则
        if m and len(m.group(1)) > 3:
            return "```" + m.group(2)
        return line

    t = "\n".join(_collapse_fence_line(L) for L in t.split("\n"))

    # ATX 标题转为粗体（lark_md 不支持 ### 标题语法）
    t = _RE_ATX_HEADING.sub(r"**\2**", t)  # 性能优化：预编译正则

    from miniagent.feishu.cards.gfm_table import (
        find_gfm_table_block,
        gfm_table_block_to_bullet_list,
    )

    lines = t.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        found = find_gfm_table_block(lines, i)
        if found is not None:
            bi, bj = found
            bullet = gfm_table_block_to_bullet_list(lines[bi:bj])
            if bullet:
                out.append(bullet)
            i = bj
            continue
        out.append(lines[i])
        i += 1
    joined = "\n".join(out)
    joined = _RE_HORIZONTAL_RULE.sub(
        "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500",
        joined,
    )  # \u6027\u80fd\u4f18\u5316\uff1a\u9884\u7f16\u8bd1\u6b63\u5219
    return joined


def _prepare_thinking_body_for_card(
    raw: str,
    *,
    apply_cap: bool = True,
    max_len: int | None = None,
) -> str:
    """思考卡片正文：折叠空行、可选长度帽、``lark_md`` 规范化（正文左对齐，不再人为段首/列表缩进）。"""
    cap = feishu_card_body_max() if max_len is None else max_len
    t = (raw or "").replace("\r", "").replace("\t", "  ")
    t = _collapse_excessive_blank_lines(t)
    if apply_cap and len(t) > cap:
        t = t[:cap] + "…"
    return _normalize_lark_md(t)


def _prepare_card_markdown(
    raw: str,
    max_len: int | None = None,
    *,
    normalize: bool = True,
) -> str:
    """最终回复等卡片正文：长度帽、制表符与可选 ``_normalize_lark_md``。"""
    cap = feishu_card_body_max() if max_len is None else max_len
    t = raw if len(raw) <= cap else raw[:cap] + "…"
    t = t.replace("\r", "").replace("\t", "  ")
    if normalize:
        return _normalize_lark_md(t)
    return t


def _prepare_thinking_markdown(raw: str) -> str:
    """思考流卡片专用：等同 ``_prepare_thinking_body_for_card`` 且启用长度帽。"""
    return _prepare_thinking_body_for_card(raw, apply_cap=True, max_len=feishu_card_thinking_max())


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
    cleaned = _prepare_thinking_markdown(raw)
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


def _feishu_reply_plain_enabled() -> bool:
    """``MINIAGENT_FEISHU_REPLY_PLAIN``：默认渲染富文本 Markdown；设为 ``1`` 时去掉常见 Markdown 标记（仍为 ``lark_md``）。"""
    return bool(get_config("feishu.reply_plain", False))


def _strip_light_markdown_for_feishu_plain(text: str) -> str:
    """弱化 Markdown 标记，减轻客户端对部分语法显示成「源码」时的观感（非完整解析器）。"""
    t = (text or "").replace("\r\n", "\n")
    t = _RE_CODE_FENCE.sub(r"\1", t)  # 性能优化：预编译正则
    t = _RE_INLINE_CODE.sub(r"\1", t)  # 性能优化：预编译正则
    prev = None
    while prev != t:
        prev = t
        t = _RE_BOLD_STAR.sub(r"\1", t)  # 性能优化：预编译正则
        t = _RE_BOLD_UNDERSCORE.sub(r"\1", t)  # 性能优化：预编译正则
    return t


def _chunk_tail_unclosed_fence(chunk: str) -> bool:
    """按行首 ``` 切换：块末尾是否落在未闭合的代码围栏内。"""
    inside = False
    for line in chunk.split("\n"):
        st = line.strip()
        if st.startswith("```"):
            inside = not inside
    return inside


def _feishu_chunk_cut_index(text: str, cap: int) -> int:
    """在 ``cap`` 附近选换行切分；尽量不把切分落在未闭合的 ``` 围栏内。"""
    if len(text) <= cap:
        return len(text)
    cut = text.rfind("\n", 0, cap)
    if cut < max(cap // 2, 1):
        cut = cap
    spare = max(8000, cap // 8)
    limit = min(len(text), cut + spare)
    while cut < limit:
        if not _chunk_tail_unclosed_fence(text[:cut]):
            return cut
        nxt = text.find("\n", cut)
        if nxt == -1:
            return len(text)
        cut = nxt + 1
    return cut


def _chunk_feishu_card_markdown(
    reply: str,
    max_len: int | None = None,
    *,
    already_normalized: bool = False,
) -> list[str]:
    """将超长正文切成多张卡片可用的段落。

    默认分片前先做 ``_normalize_lark_md``，避免表格/零宽等在块边界上被截断后单卡语义不完整；
    ``already_normalized=True`` 时跳过规范化（用于已走 ``_prepare_thinking_body_for_card`` 的思考收尾）。
    切分时尽量避开未闭合的代码围栏。
    """
    cap = feishu_card_body_max() if max_len is None else max_len
    t = (reply or "").replace("\r", "").replace("\t", "  ")
    if not already_normalized:
        t = _normalize_lark_md(t)
    if cap <= 0 or len(t) <= cap:
        return [t]
    chunks: list[str] = []
    rest = t
    while rest:
        if len(rest) <= cap:
            chunks.append(rest)
            break
        cut = _feishu_chunk_cut_index(rest, cap)
        if cut <= 0:
            cut = min(len(rest), cap)
        chunk = rest[:cut]
        chunks.append(chunk)
        rest = rest[cut:].lstrip("\n")
    return chunks


def _feishu_interactive_card_dict(
    header_title: str, body_markdown: str, template: str
) -> dict[str, Any]:
    """构造飞书交互卡片 JSON 结构。"""
    from miniagent.feishu.cards.builder import build_interactive_card

    return build_interactive_card(header_title, body_markdown, template)


def _thinking_interactive_card_dict(
    cleaned_markdown: str,
    template: str,
    *,
    session_key: str | None = None,
    confirmation_engine: Any | None = None,
) -> dict[str, Any]:
    """构造思考内容交互卡片（可能包含确认按钮）。"""
    from miniagent.feishu.cards.builder import confirmation_buttons, thinking_card_dict

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
    from miniagent.feishu.im_send import patch_im_message_async

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
    import time

    chat_id = _normalize_im_receive_chat_id(chat_id)
    if not chat_id:
        return

    # 工具段提取：仅 new_round=True 且旧轮有工具时需要保留旧轮 LLM 正文（不含工具段）。
    _TOOL_MARKER = "\n\n**工具**"
    existing = getattr(st, "feishu_stream_accumulated", "") or ""
    tool_section = ""
    if _TOOL_MARKER in existing and getattr(st, "feishu_tool_section_started", False):
        tool_section = existing[existing.index(_TOOL_MARKER) :]

    if new_round:
        # 复用同一张思考卡：若上一轮有工具段，先保留旧轮 LLM 正文（不含工具段），
        # 用分隔符衔接新轮；新轮的 LLM 正文写入后再重新附上工具段，避免新旧轮重复。
        # 若上一轮无工具（tool_section 为空），直接初始化，由新轮 LLM 内容填充。
        _round_separator = bool(tool_section)
        if _round_separator:
            idx = existing.index(_TOOL_MARKER)
            llm_only = existing[:idx].rstrip()
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
    else:
        _round_separator = False

    if _round_separator:
        # 新轮已写入分隔符（仅含旧轮 LLM 正文），追加新轮 LLM 正文并重新附上工具段。
        st.feishu_stream_accumulated += markdown or ""
        st.feishu_stream_accumulated += tool_section
    else:
        # 非新一轮：不重建 tool_section，避免与 append 路径已 PATCH 到卡上的工具重复。
        # 若有 pending header（如 "[执行]"），预添加到卡片正文中。
        pending_hdr = getattr(st, "feishu_pending_header", "") or ""
        if pending_hdr:
            st.feishu_stream_accumulated = f"**{pending_hdr}**\n\n{markdown or ''}"
            st.feishu_pending_header = ""
        else:
            st.feishu_stream_accumulated = markdown or ""
    st.feishu_stream_llm_len = len(markdown or "")

    # 冲刷缓冲的工具行（在 LLM 内容设置后、卡片创建前）
    pending = getattr(st, "feishu_pending_tool_lines", None)
    if pending:
        for tool_addition in pending:
            st.feishu_stream_accumulated += tool_addition
        st.feishu_pending_tool_lines = []
        st.feishu_tool_section_started = True

    _sk = getattr(st, "feishu_session_key", None) or None
    card_json = _thinking_card_json_cached(
        st,
        st.feishu_stream_accumulated,
        template,
        _sk,
        confirmation_engine,
    )

    if not st.feishu_thinking_message_id:
        r_mid = getattr(st, "feishu_reply_to_message_id", None)
        r_thr = bool(getattr(st, "feishu_reply_in_thread", False))
        # ✅ 使用异步版本：创建卡片时不阻塞事件循环
        mid = await _create_interactive_thinking_message_async(
            config,
            chat_id,
            card_json,
            reply_to_message_id=r_mid,
            reply_in_thread=r_thr,
        )
        if mid:
            st.feishu_thinking_message_id = mid
            st.feishu_last_patch_monotonic = time.monotonic()
            st.feishu_last_patched_char_len = len(markdown)
            st.feishu_last_sent_card_json = card_json
        return

    now = time.monotonic()
    delta_t = now - st.feishu_last_patch_monotonic
    delta_c = len(markdown) - st.feishu_last_patched_char_len

    # ✅ 智能节流：重要内容立即 PATCH，长文本动态增加预算
    need_patch = (
        delta_t >= FEISHU_THINKING_PATCH_MIN_INTERVAL_S
        or delta_c >= FEISHU_THINKING_PATCH_MIN_CHAR_DELTA
        or _is_important_content_for_immediate_patch(markdown)  # 重要内容立即更新
    )

    # 动态调整预算：长文本增加 PATCH 次数
    st.feishu_patch_budget = _adjust_patch_budget_dynamically(
        len(st.feishu_stream_accumulated), st.feishu_patch_budget
    )

    card_changed = card_json != getattr(st, "feishu_last_sent_card_json", None)
    if card_changed and need_patch and st.feishu_patch_budget > 0:
        # ✅ 使用异步版本：PATCH 更新时不阻塞事件循环
        if await _patch_interactive_thinking_message_async(
            config, st.feishu_thinking_message_id, card_json
        ):
            st.feishu_patch_budget -= 1
            st.feishu_last_patch_monotonic = now
            st.feishu_last_patched_char_len = len(markdown)
            st.feishu_last_sent_card_json = card_json


async def finalize_feishu_thinking_stream(
    config: FeishuConfig,
    chat_id: str,
    template: str,
    st: Any,
    *,
    confirmation_engine: Any | None = None,
) -> None:
    """一轮 LLM 流结束或非合并的非流式块前：PATCH 首张卡片为正文第一段；超长则追加多张「思考续页」卡片。"""
    chat_id = _normalize_im_receive_chat_id(chat_id)
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
    prep = _prepare_thinking_body_for_card(acc, apply_cap=False)
    chunks = _chunk_feishu_card_markdown(prep, already_normalized=True)
    if not chunks:
        return
    nch = len(chunks)
    first_body = _prepare_card_markdown(chunks[0], normalize=False)
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
            body = _prepare_card_markdown(chunks[j], normalize=False)
            title = f"💭 思考中 ({j + 1}/{nch})"
            card = _feishu_interactive_card_dict(title, body, template)
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
    chat_id = _normalize_im_receive_chat_id(chat_id)
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
    chat_id = _normalize_im_receive_chat_id(chat_id)
    if not chat_id:
        _logger.debug("跳过发送思考：空的 chat_id")
        return

    try:
        cleaned = _prepare_thinking_markdown(thinking)
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
    chat_id = _normalize_im_receive_chat_id(chat_id)
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
    cleaned = _prepare_thinking_markdown(body)
    # 使用 "🤖 Mini Agent" 卡片头，与 .help 命令输出格式一致
    card_json = json.dumps(
        _feishu_interactive_card_dict("🤖 Mini Agent", cleaned, template), ensure_ascii=False
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
    "feishu_outbound_reply_params",
    "FeishuMediaHandler",
    "FeishuPollState",
    "start_feishu_poll_server",
    "feishu_card_body_max",
    "push_feishu_thinking_stream",
    "finalize_feishu_thinking_stream",
    "append_feishu_thinking_same_card",
    "send_reflection_card",
]
