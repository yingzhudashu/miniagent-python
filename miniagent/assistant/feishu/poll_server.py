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
``miniagent.assistant.infrastructure.message_queue``。

**已拆分模块**：
- feishu/feishu_dedup.py: 消息去重逻辑（内存+磁盘）— 本模块已导入使用
- feishu/ws_client.py: WebSocket 连接管理
- feishu/ws_health.py: WebSocket 健康监督
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

from miniagent.agent.logging import get_logger
from miniagent.agent.types.error_prefix import SUCCESS_PREFIX
from miniagent.assistant.feishu.outbound_delivery import (
    _feishu_reply_plain_enabled,
    _post_interactive_message,
    _post_interactive_message_async,
    _post_text_message,
    _send_interactive_reply_cards,
    _send_plain_text_chunks,
    _send_reply,
    feishu_outbound_reply_params,
)
from miniagent.assistant.feishu.poll_state import (
    FeishuMediaHandler,
    FeishuPollState,
    _extract_post_media_items,
    _feishu_media_reply_indicates_failure,
    _parse_feishu_media_payload,
)
from miniagent.assistant.feishu.thinking_delivery import (
    _create_interactive_thinking_message,
    _create_interactive_thinking_message_async,
    _patch_interactive_thinking_message,
    _patch_interactive_thinking_message_async,
    _send_thinking,
    _thinking_card_json_cached,
    append_feishu_thinking_same_card,
    finalize_feishu_thinking_stream,
    push_feishu_thinking_stream,
    send_reflection_card,
)
from miniagent.assistant.infrastructure.json_config import get_config
from miniagent.ui.feishu.types import FeishuConfig, FeishuInboundText

_logger = get_logger(__name__)

# --- 出站：reply 参数、interactive/text（经 im_send）---
# 入站文本 handler：单参数 ``FeishuInboundText``，返回回复正文。
FeishuTextMessageHandler = Callable[[FeishuInboundText], Awaitable[str]]


class _FeishuPollCallbacks:
    """拥有飞书 SDK 同步回调及其异步任务分派策略。"""

    def __init__(
        self,
        config: FeishuConfig,
        message_handler: FeishuTextMessageHandler,
        runtime_state: FeishuPollState,
        message_queue: Any,
        media_handler: FeishuMediaHandler | None,
    ) -> None:
        self.config = config
        self.message_handler = message_handler
        self.state = runtime_state
        self.queue = message_queue
        self.media_handler = media_handler

    def on_message_receive(self, event: Any) -> None:
        """验证、认领并按消息类型调度一个 SDK 入站事件。"""
        message_id = ""
        try:
            self.state.ws_health.touch_inbound()
            message = getattr(getattr(event, "event", None), "message", None)
            if message is None:
                return
            message_id = message.message_id or ""
            if not message_id:
                _logger.warning("收到无 message_id 的事件，跳过")
                return
            if not self.state.deduplicator.try_begin_processing(message_id):
                return
            create_time = self._create_time(message)
            if create_time and time.time() - create_time > get_config("feishu.max_message_age", 600):
                self.state.deduplicator.release_processing(message_id)
                return
            sender = getattr(getattr(event, "event", None), "sender", None)
            sender_id = (
                getattr(getattr(sender, "sender_id", None), "open_id", "") or ""
            )
            context = {
                "message_id": message_id,
                "chat_id": message.chat_id or "",
                "sender_id": sender_id,
                "chat_type": getattr(message, "chat_type", "group") or "group",
                "message": message,
                "create_time": create_time,
            }
            message_type = message.message_type or ""
            if message_type in ("text", "interactive"):
                self._route_text(message_type, message.content or "", context)
            elif message_type in ("file", "image") and self.media_handler:
                self._route_media(message_type, message.content or "", context)
            elif message_type == "post" and self.media_handler:
                self._route_post(message.content or "", context)
            else:
                self.state.deduplicator.release_processing(message_id)
        except Exception as error:
            _logger.error("事件处理异常: %s", error)
            if message_id:
                self.state.deduplicator.abandon_processing_claim(message_id)

    @staticmethod
    def _create_time(message: Any) -> int:
        raw = getattr(message, "create_time", None) or 0
        try:
            return int(raw) if raw else 0
        except (ValueError, TypeError):
            return 0

    @staticmethod
    def _extract_text(message_type: str, content: str) -> str:
        if message_type == "interactive" and get_config("feishu.card_extract_inbound", True):
            from miniagent.assistant.feishu.cards.extract import inbound_text_from_message

            return inbound_text_from_message(message_type, content) or ""
        if message_type != "text":
            return ""
        try:
            return json.loads(content).get("text", "")
        except (json.JSONDecodeError, TypeError):
            return content

    def _route_text(self, message_type: str, content: str, context: dict[str, Any]) -> None:
        text = self._extract_text(message_type, content)
        message_id = context["message_id"]
        if not text.strip():
            self.state.deduplicator.release_processing(message_id)
            return
        message = context["message"]
        inbound = FeishuInboundText(
            text=text,
            chat_id=context["chat_id"],
            sender_id=context["sender_id"],
            chat_type=context["chat_type"],
            message_id=message_id,
            root_id=(message.root_id or "").strip() or None,
            parent_id=(message.parent_id or "").strip() or None,
            thread_id=(message.thread_id or "").strip() or None,
            create_time=context["create_time"],
        )
        if text.lstrip().startswith("/"):
            self._spawn_dispatch(inbound.chat_id, self._text_job(inbound, [message_id]))
            return
        channel = _resolve_feishu_confirmation_channel(
            self.state, inbound.chat_id, inbound.sender_id, inbound.chat_type
        )
        if self._respond_to_clarification(channel, text):
            self.state.deduplicator.release_processing(message_id)
            return
        self.state.spawn_callback_task(self._schedule_debounced(inbound))

    @staticmethod
    def _respond_to_clarification(channel: Any, text: str) -> bool:
        if not channel or not channel.has_pending:
            return False
        from miniagent.agent.types.confirmation import ConfirmationResult, ConfirmationStage

        if channel.pending.stage != ConfirmationStage.CLARIFICATION:
            return False
        channel.respond(ConfirmationResult.clarification_reply(text))
        return True

    async def _schedule_debounced(self, inbound: FeishuInboundText) -> None:
        from miniagent.assistant.feishu.message_debounce import feishu_message_debounce_ms

        async def flush(merged: FeishuInboundText, claim_ids: list[str]) -> None:
            self._spawn_dispatch(merged.chat_id, self._text_job(merged, claim_ids))

        await self.state.debouncer.schedule(
            inbound, debounce_ms=feishu_message_debounce_ms(), on_flush=flush
        )

    def _text_job(self, inbound: FeishuInboundText, claim_ids: list[str]) -> Awaitable[None]:
        async def job() -> None:
            success = False
            try:
                reply = await self.message_handler(inbound)
                if reply:
                    reply_id, in_thread = feishu_outbound_reply_params(
                        inbound.message_id, inbound.thread_id
                    )
                    await _send_reply(
                        self.config,
                        inbound.chat_id,
                        reply,
                        reply_to_message_id=reply_id,
                        reply_in_thread=in_thread,
                    )
                success = True
            except Exception as error:
                _logger.error("处理消息失败: %s", error)
            finally:
                finalizer = (
                    self.state.deduplicator.release_processing
                    if success
                    else self.state.deduplicator.abandon_processing_claim
                )
                for claim_id in claim_ids:
                    finalizer(claim_id)

        return job()

    def _spawn_dispatch(self, chat_id: str, job: Awaitable[None]) -> None:
        self.state.spawn_callback_task(self.queue.dispatch(chat_id, job))

    def _route_media(self, message_type: str, content: str, context: dict[str, Any]) -> None:
        parsed = _parse_feishu_media_payload(message_type, content)
        if not parsed:
            self.state.deduplicator.release_processing(context["message_id"])
            return
        resource_type, file_key, suggested_name = parsed
        thread_id = (context["message"].thread_id or "").strip() or None
        self._spawn_dispatch(
            context["chat_id"],
            self._media_job(context, message_type, resource_type, file_key, suggested_name, thread_id),
        )

    def _media_job(
        self,
        context: dict[str, Any],
        message_type: str,
        resource_type: str,
        file_key: str,
        suggested_name: str,
        thread_id: str | None,
    ) -> Awaitable[None]:
        """创建单媒体后台任务，并按真实成功状态提交或释放去重 claim。

        返回的协程由消息队列按 ``chat_id`` 串行执行。发送确认消息属于可选副作用；
        处理失败或取消前未成功时放弃 claim，使上游重投仍可重新处理。
        """
        media_handler = self.media_handler
        assert media_handler is not None

        async def job() -> None:
            success = False
            try:
                reply = await media_handler(
                    self.config,
                    context["message_id"],
                    context["chat_id"],
                    context["sender_id"],
                    context["chat_type"],
                    message_type,
                    file_key,
                    suggested_name,
                    resource_type,
                    thread_id,
                )
                success = not _feishu_media_reply_indicates_failure(reply)
                if success and reply and not get_config("feishu.media.silent_reply", False):
                    reply_id, in_thread = feishu_outbound_reply_params(
                        context["message_id"], thread_id
                    )
                    await _send_reply(
                        self.config,
                        context["chat_id"],
                        reply,
                        reply_to_message_id=reply_id,
                        reply_in_thread=in_thread,
                    )
            except Exception as error:
                _logger.error("处理飞书媒体失败: %s", error)
            finally:
                finalizer = self.state.deduplicator.release_processing if success else self.state.deduplicator.abandon_processing_claim
                finalizer(context["message_id"])

        return job()

    def _route_post(self, content: str, context: dict[str, Any]) -> None:
        items = _extract_post_media_items(content)
        if not items:
            self.state.deduplicator.release_processing(context["message_id"])
            return
        thread_id = (context["message"].thread_id or "").strip() or None
        self._spawn_dispatch(context["chat_id"], self._post_job(context, items, thread_id))

    def _post_job(
        self, context: dict[str, Any], items: list[Any], thread_id: str | None
    ) -> Awaitable[None]:
        """创建富文本媒体批处理任务，全部条目成功后才确认去重 claim。

        同一 post 内媒体严格按顺序处理；任一条目返回失败即停止，避免部分成功被
        错误标记为已消费。汇总回复仅在完整成功且未启用静默模式时发送。
        """
        media_handler = self.media_handler
        assert media_handler is not None

        async def job() -> None:
            success = False
            replies = []
            try:
                for resource_type, file_key, suggested_name in items:
                    reply = await media_handler(
                        self.config,
                        context["message_id"],
                        context["chat_id"],
                        context["sender_id"],
                        context["chat_type"],
                        "post",
                        file_key,
                        suggested_name,
                        resource_type,
                        thread_id,
                    )
                    if _feishu_media_reply_indicates_failure(reply):
                        return
                    if reply:
                        replies.append(reply)
                if replies and not get_config("feishu.media.silent_reply", False):
                    reply_id, in_thread = feishu_outbound_reply_params(context["message_id"], thread_id)
                    await _send_reply(
                        self.config,
                        context["chat_id"],
                        "\n".join(replies),
                        reply_to_message_id=reply_id,
                        reply_in_thread=in_thread,
                    )
                success = True
            except Exception as error:
                _logger.error("处理飞书 post 媒体失败: %s", error)
            finally:
                finalizer = self.state.deduplicator.release_processing if success else self.state.deduplicator.abandon_processing_claim
                finalizer(context["message_id"])

        return job()

    @staticmethod
    def _toast(response_type: str, content: str) -> Any:
        from lark_oapi.event.callback.model.p2_card_action_trigger import (
            CallBackToast,
            P2CardActionTriggerResponse,
        )

        response = P2CardActionTriggerResponse()
        toast = CallBackToast()
        toast.type = response_type
        toast.content = content
        response.toast = toast
        return response

    def on_card_action(self, event: Any) -> Any:
        """解析卡片动作，优先处理确认控制命令，否则投递消息队列。"""
        error = self._toast("error", "Mini Agent：缺少 miniagent_text 或 chat_id（请在按钮 value 中提供）")
        try:
            self.state.ws_health.touch_inbound()
            payload = getattr(event, "event", None)
            if payload is None:
                return error
            action = getattr(payload, "action", None)
            value = dict(getattr(action, "value", None) or {}) if action else {}
            from miniagent.assistant.feishu.cards.action_router import (
                inbound_text_from_card_action_value,
            )

            text = (inbound_text_from_card_action_value(value) or "").strip()
            context = getattr(payload, "context", None)
            chat_id = str(value.get("chat_id") or getattr(context, "open_chat_id", "") or "").strip()
            operator = getattr(payload, "operator", None)
            sender_id = str(getattr(operator, "open_id", "") or "").strip()
            dedupe_key = str(value.get("dedupe_key") or "").strip()
            if dedupe_key and self.state.card_actions.should_skip(dedupe_key):
                return self._toast("info", "已处理（重复操作已忽略）")
            if not text or not chat_id:
                return error
            confirmation_response = self._handle_card_confirmation(
                text, chat_id, sender_id
            )
            if confirmation_response is not None:
                return confirmation_response
            inbound = FeishuInboundText(
                text=text,
                chat_id=chat_id,
                sender_id=sender_id,
                chat_type=str(value.get("chat_type") or "group"),
                message_id=str(value.get("message_id") or "").strip(),
            )
            try:
                self._spawn_dispatch(chat_id, self._card_job(inbound))
            except RuntimeError:
                return self._toast("error", "Mini Agent：无运行中的事件循环，无法调度")
            return self._toast("info", "已提交处理")
        except Exception as exception:
            _logger.warning("卡片动作入口异常: %s", exception)
            return self._toast("error", f"Mini Agent：{exception}")

    def _handle_card_confirmation(
        self, text: str, chat_id: str, sender_id: str
    ) -> Any | None:
        if text not in ("/confirm", "/reject") and not text.startswith("/adjust "):
            return None
        channel = _resolve_feishu_confirmation_channel(
            self.state, chat_id, sender_id, "group"
        )
        if channel is None or not channel.has_pending:
            return None
        from miniagent.agent.types.confirmation import ConfirmationResult

        if text == "/confirm":
            channel.respond(ConfirmationResult.confirm())
            return self._toast("success", "✅ 已确认，继续执行")
        if text == "/reject":
            channel.respond(ConfirmationResult.reject())
            return self._toast("warning", "⚠️ 已拒绝，取消当前操作")
        adjustment = text[len("/adjust ") :].strip()
        if not adjustment:
            return None
        channel.respond(ConfirmationResult.adjust(adjustment))
        suffix = "…" if len(adjustment) > 40 else ""
        return self._toast(
            "success", f"{SUCCESS_PREFIX} 已调整：{adjustment[:40]}{suffix}"
        )

    def _card_job(self, inbound: FeishuInboundText) -> Awaitable[None]:
        async def job() -> None:
            try:
                reply = await self.message_handler(inbound)
                if reply:
                    reply_id, in_thread = feishu_outbound_reply_params(
                        inbound.message_id or None, inbound.thread_id
                    )
                    await _send_reply(
                        self.config,
                        inbound.chat_id,
                        reply,
                        reply_to_message_id=reply_id,
                        reply_in_thread=in_thread,
                    )
            except Exception as error:
                _logger.warning("卡片动作调度 Agent 失败: %s", error)

        return job()


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


async def _cleanup_feishu_ws_tasks(ws_client: Any, ping_task: asyncio.Task[Any] | None) -> None:
    """取消并消费 SDK ping/receive 任务，避免未检索异常。"""
    if ping_task is not None and not ping_task.done():
        ping_task.cancel()
        try:
            await ping_task
        except asyncio.CancelledError:
            pass
        except Exception as error:
            _logger.debug("Ping任务清理失败（非关键）: %s", error)
    try:
        from websockets.exceptions import ConnectionClosedOK
    except ImportError:
        ConnectionClosedOK = Exception  # type: ignore[misc, assignment]
    try:
        receive_task = getattr(ws_client, "receive_task", None)
        if receive_task is None:
            return
        if not receive_task.done():
            receive_task.cancel()
            try:
                await receive_task
            except (asyncio.CancelledError, ConnectionClosedOK):
                pass
            except Exception as error:
                _logger.debug("接收任务清理失败（非关键）: %s", error)
        else:
            try:
                receive_task.result()
            except Exception as error:
                _logger.debug("读取接收任务结果失败（清理路径）: %s", error)
    except Exception as error:
        _logger.debug("清理接收任务失败（清理路径）: %s", error)


def _build_poll_event_handler(config: FeishuConfig, callbacks: _FeishuPollCallbacks) -> Any:
    """构造 SDK 事件分派器，并按配置注册卡片动作。"""
    from lark_oapi.event.dispatcher_handler import EventDispatcherHandler

    builder = EventDispatcherHandler.builder(
        config.encrypt_key or "", config.verification_token or ""
    ).register_p2_im_message_receive_v1(callbacks.on_message_receive)
    if get_config("feishu.card_action_router", True):
        builder = builder.register_p2_card_action_trigger(callbacks.on_card_action)
    return builder.build()


async def _run_poll_ws_session(
    config: FeishuConfig,
    event_handler: Any,
    runtime_state: FeishuPollState,
    shutdown_event: asyncio.Event,
) -> None:
    """创建、监督并清理单次 WebSocket 会话。"""
    import lark_oapi.ws.client as sdk_ws_module
    from lark_oapi.core.enum import LogLevel

    from miniagent.assistant.feishu.ws_client import FeishuWsClient
    from miniagent.assistant.feishu.ws_health import supervise_feishu_ws_session

    sdk_ws_module.loop = asyncio.get_running_loop()
    ws_client: FeishuWsClient | None = None
    ping_task: asyncio.Task[Any] | None = None
    try:
        ws_client = FeishuWsClient(
            app_id=config.app_id,
            app_secret=config.app_secret,
            event_handler=event_handler,
            log_level=LogLevel.ERROR,
        )
        runtime_state.client = ws_client
        runtime_state.app_id = config.app_id
        _logger.info("WebSocket 长轮询模式已启动（无需公网 IP）")
        _logger.info("消息会通过 WebSocket 自动从飞书服务器拉取")
        await ws_client._connect()
        ping_task = asyncio.create_task(ws_client._ping_loop())
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
    finally:
        await _cleanup_feishu_ws_tasks(ws_client, ping_task)
        await runtime_state.reset()


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
    # 任何实例残留连接一律关闭后重建，避免外层重连误判为断线空转。
    if runtime_state.client is not None:
        if runtime_state.app_id != config.app_id:
            _logger.info("存在不同 appId 的 WSClient (%s)，先关闭", runtime_state.app_id)
        else:
            _logger.warning("检测到残留 WebSocket 客户端（与当前 appId 相同），将关闭后重建")
        await runtime_state.reset()

    # 加载 SDK
    try:
        import lark_oapi  # noqa: F401
    except ImportError as import_error:
        _logger.error("请安装 lark-oapi: pip install lark-oapi (%s)", import_error)
        raise

    callbacks = _FeishuPollCallbacks(
        config, message_handler, runtime_state, message_queue, media_handler
    )
    event_handler = _build_poll_event_handler(config, callbacks)

    shutdown_event = asyncio.Event()
    runtime_state.shutdown_event = shutdown_event
    try:
        try:
            await _run_poll_ws_session(config, event_handler, runtime_state, shutdown_event)
        except (KeyboardInterrupt, asyncio.CancelledError):
            _logger.info("收到退出信号")
            shutdown_event.set()
            raise

    except Exception as e:
        _logger.error("WebSocket 启动失败: %s", e)
        raise
    finally:
        runtime_state.shutdown_event = None




# --- lark_md / GFM：规范化、宽表降级、卡片分片与 PATCH 节流（与 ThinkingDisplay 输出对齐）---
# 单条「思考」卡片：流式时用 PATCH 更新同一 message_id；飞书对单条消息可 PATCH 次数有限，须节流。
# 节流参数从JSON配置读取，默认值已优化为更流畅的流式体验（间隔更短、字符增量更小）

__all__ = [
    "_create_interactive_thinking_message",
    "_create_interactive_thinking_message_async",
    "_feishu_reply_plain_enabled",
    "_patch_interactive_thinking_message",
    "_patch_interactive_thinking_message_async",
    "_post_interactive_message",
    "_post_interactive_message_async",
    "_post_text_message",
    "_send_reply",
    "_send_interactive_reply_cards",
    "_send_plain_text_chunks",
    "_send_thinking",
    "_thinking_card_json_cached",
    "FeishuMediaHandler",
    "FeishuPollState",
    "start_feishu_poll_server",
    "push_feishu_thinking_stream",
    "finalize_feishu_thinking_stream",
    "append_feishu_thinking_same_card",
    "send_reflection_card",
]
