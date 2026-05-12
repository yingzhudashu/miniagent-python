"""Mini Agent Python — 飞书 WebSocket 长轮询

使用飞书 SDK WSClient 长轮询模式接收事件推送。

核心机制（对齐 OpenClaw）：
- 单客户端单例：防止多实例导致事件路由不确定
- 内存+磁盘双重去重：防止重复处理同一消息
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
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from miniagent.feishu.types import FeishuConfig, FeishuInboundText
from miniagent.infrastructure.logger import get_logger

_logger = get_logger(__name__)

# --- 出站：reply 参数、interactive/text（经 im_send）---
# 入站文本 handler：单参数 ``FeishuInboundText``，返回回复正文。
FeishuTextMessageHandler = Callable[[FeishuInboundText], Awaitable[str]]


def feishu_outbound_reply_params(
    trigger_message_id: str | None,
    thread_id: str | None = None,
) -> tuple[str | None, bool]:
    """是否使用飞书「回复消息」API（``im/v1/messages/:message_id/reply``）。

    环境变量：
    - ``MINIAGENT_FEISHU_REPLY_TARGET``：``create``（默认，会话内新消息）或 ``reply``；其它值视为 ``create``。
    - ``MINIAGENT_FEISHU_REPLY_IN_THREAD``：显式 ``1``/``true`` 等为真；``0``/``false`` 等为假；**未设置**且入站
      ``thread_id`` 非空时，在 ``reply`` 模式下默认 ``reply_in_thread=True``。

    Returns:
        ``(reply_parent_message_id_or_None, reply_in_thread)``
    """
    mode = (os.environ.get("MINIAGENT_FEISHU_REPLY_TARGET") or "create").strip().lower()
    if mode not in ("create", "reply"):
        return None, False
    if mode != "reply":
        return None, False
    mid = (trigger_message_id or "").strip()
    if not mid:
        return None, False
    raw_thr = (os.environ.get("MINIAGENT_FEISHU_REPLY_IN_THREAD") or "").strip().lower()
    if raw_thr in ("0", "false", "no", "off"):
        thr = False
    elif raw_thr in ("1", "true", "yes", "on"):
        thr = True
    else:
        thr = bool((thread_id or "").strip())
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


def _parse_feishu_media_payload(
    msg_type: str, content_str: str
) -> tuple[str, str, str] | None:
    """解析 file/image 消息的 file_key 与建议文件名。返回 (resource_type, file_key, suggested_name)。"""
    try:
        d = json.loads(content_str or "{}")
    except (json.JSONDecodeError, TypeError):
        return None
    if msg_type == "file":
        fk = d.get("file_key")
        name = d.get("file_name") or d.get("name") or "download.bin"
        if not fk:
            return None
        return ("file", str(fk), str(name))
    if msg_type == "image":
        ik = d.get("image_key")
        if not ik:
            return None
        return ("image", str(ik), "image.bin")
    return None


def _extract_post_media_items(content_str: str) -> list[tuple[str, str, str]]:
    """从 post 富文本 JSON 中递归收集 (resource_type, file_key_or_image_key, suggested_name)。"""
    out: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()

    def walk(node: Any) -> None:
        """深度优先遍历 post JSON 子树，去重收集图片与附件 key。"""
        if isinstance(node, dict):
            tag = node.get("tag")
            if tag == "img":
                ik = node.get("image_key") or node.get("image_token")
                if ik and ("image", str(ik)) not in seen:
                    seen.add(("image", str(ik)))
                    out.append(("image", str(ik), "image.bin"))
            elif tag == "media":
                fk = node.get("file_key")
                if fk and ("file", str(fk)) not in seen:
                    seen.add(("file", str(fk)))
                    nm = node.get("file_name") or node.get("name") or "download.bin"
                    out.append(("file", str(fk), str(nm)))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for x in node:
                walk(x)

    try:
        root = json.loads(content_str or "{}")
    except (json.JSONDecodeError, TypeError):
        return []
    walk(root)
    return out


# --- WebSocket 单例：reset 供关停或重连前释放 SDK Client ---


async def reset_feishu_ws_singleton() -> None:
    """关闭并清空本模块持有的飞书 WS 单例，便于外层重连前重建 Client。"""
    global _singleton_client, _singleton_app_id
    c = _singleton_client
    _singleton_client = None
    _singleton_app_id = None
    if c is None:
        return
    try:
        await c._disconnect()
    except Exception as e:
        _logger.debug("reset_feishu_ws_singleton: %s", e)


# --- 入站 message_id：内存 claim + 磁盘 JSON（防重复进队列；TTL 见下）---
# 去重配置
DEDUP_TTL_MS = 5 * 60 * 1000  # 5 分钟
DEDUP_MAX_SIZE = 2000

# 单例状态（每进程一套 WS；防多客户端抢事件，与 OpenClaw 对齐）
_singleton_client: Any = None
_singleton_app_id: str | None = None

# 内存去重
_processing_claims: dict[str, float] = {}

# 磁盘去重
_state_dir = os.path.join(
    os.environ.get("MINI_AGENT_STATE", os.path.join(os.getcwd(), "workspaces")),
    "feishu",
    "dedup",
)
_dedup_file = os.path.join(_state_dir, "processed.json")
_disk_dedup: dict[str, float] = {}


# ─── 去重管理 ───


def _ensure_state_dir():
    """确保状态目录存在。"""
    os.makedirs(_state_dir, exist_ok=True)


def _load_disk_dedup():
    """加载磁盘去重数据。"""
    global _disk_dedup
    try:
        _ensure_state_dir()
        if os.path.isfile(_dedup_file):
            with open(_dedup_file, encoding="utf-8") as f:
                _disk_dedup = json.load(f)
    except Exception:
        _disk_dedup = {}


def _save_disk_dedup():
    """保存磁盘去重数据。"""
    try:
        _ensure_state_dir()
        with open(_dedup_file, "w", encoding="utf-8") as f:
            json.dump(_disk_dedup, f, indent=2)
    except Exception:
        pass


def _resolve_dedup_key(message_id: str) -> str:
    """解析去重键。"""
    return f"mini-agent:{message_id.strip()}"


def _prune_claims():
    """清理过期去重条目。"""
    cutoff = time.time() - DEDUP_TTL_MS / 1000.0
    to_remove = [k for k, v in _processing_claims.items() if v < cutoff]
    for k in to_remove:
        del _processing_claims[k]

    to_remove = [k for k, v in _disk_dedup.items() if v < cutoff]
    for k in to_remove:
        del _disk_dedup[k]

    if len(_processing_claims) + len(_disk_dedup) > DEDUP_MAX_SIZE * 2:
        _save_disk_dedup()


def try_begin_processing(message_id: str) -> bool:
    """尝试获取消息处理权。

    Returns:
        True = 首次处理，可以处理；False = 重复/处理中，跳过
    """
    key = _resolve_dedup_key(message_id)
    if not key:
        return True

    now = time.time()
    _prune_claims()

    # 1. 检查磁盘去重
    if key in _disk_dedup:
        return False

    # 2. 检查内存处理中
    if key in _processing_claims:
        return False

    # 获取处理权
    _processing_claims[key] = now
    _prune_claims()
    return True


def release_processing(message_id: str):
    """释放处理权 + 记录到磁盘去重。"""
    key = _resolve_dedup_key(message_id)
    if not key:
        return

    _processing_claims.pop(key, None)
    _disk_dedup[key] = time.time()

    # 限制磁盘去重大小
    if len(_disk_dedup) > DEDUP_MAX_SIZE:
        sorted_items = sorted(_disk_dedup.items(), key=lambda x: x[1])
        to_remove = len(sorted_items) // 5  # 删除最老的 20%
        for k, _ in sorted_items[:to_remove]:
            del _disk_dedup[k]
        _save_disk_dedup()


def abandon_processing_claim(message_id: str) -> None:
    """仅丢弃内存中的处理权，不写入磁盘去重（可恢复失败时调用，避免永久跳过）。"""
    key = _resolve_dedup_key(message_id)
    if not key:
        return
    _processing_claims.pop(key, None)


def _feishu_media_reply_indicates_failure(reply: str | None) -> bool:
    """media_handler 用「⚠️」前缀表示不可落盘的失败类回复。"""
    if not reply:
        return False
    return reply.lstrip().startswith("\u26a0\ufe0f")


# 初始化磁盘去重
_load_disk_dedup()


# ─── 消息队列 ───
# 已由 miniagent.infrastructure.message_queue.MessageQueueManager 统一管理


# ─── 长轮询入口：WSClient、事件回调、handler 内投递 message_queue ───
# 与 ``# ─── 消息队列 ───`` 注释呼应：此处只负责连接与解析，顺序语义由传入的 ``message_queue`` 保证。

async def start_feishu_poll_server(
    config: FeishuConfig,
    message_handler: FeishuTextMessageHandler,
    *,
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
    global _singleton_client, _singleton_app_id

    # #region agent log
    try:
        from miniagent.infrastructure.debug_ndjson import agent_debug_log

        agent_debug_log(
            hypothesis_id="E",
            location="poll_server.py:start_feishu_poll_server",
            message="poll_server_entry",
            data={"app_id_len": len((config.app_id or "").strip())},
        )
    except Exception:
        pass
    # #endregion

    # 任何残留单例一律关闭后重建，避免「同 appId 直接 return」导致外层重连误判为断线空转。
    if _singleton_client is not None:
        if _singleton_app_id != config.app_id:
            _logger.info("存在不同 appId 的 WSClient (%s)，先关闭", _singleton_app_id)
        else:
            _logger.warning(
                "检测到残留 WebSocket 单例（与当前 appId 相同），将关闭后重建"
            )
        await reset_feishu_ws_singleton()

    # 加载 SDK
    try:
        import lark_oapi as lark
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
        except Exception:
            pass
        # #endregion
        _logger.error("请安装 lark-oapi: pip install lark-oapi (%s)", e)
        raise

    # 同步回调（SDK 要求 sync），内部通过 asyncio.create_task 调度 async 逻辑
    def on_message_receive(event: P2ImMessageReceiveV1) -> None:
        """处理 im.message.receive_v1 事件。"""
        try:
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

            chat_id = message.chat_id or ""
            sender = event.event.sender
            sender_id = (sender.sender_id.open_id or "") if sender and sender.sender_id else ""
            msg_type = message.message_type or ""
            chat_type = getattr(event.event.message, "chat_type", "group") or "group"

            content_str = message.content or ""

            if msg_type == "text":
                text = ""
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
                )

                async def _handle():
                    finalized = False
                    try:
                        reply = await message_handler(inbound)
                        if reply:
                            r_mid, r_thr = feishu_outbound_reply_params(
                                inbound.message_id, inbound.thread_id
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
                            release_processing(message_id)
                        else:
                            abandon_processing_claim(message_id)

                # 点命令走控制面：不得与 Agent 同锁排队，否则卡死时无法在飞书侧下发 `.abort` 等。
                if text.lstrip().startswith("."):
                    asyncio.create_task(_handle())
                else:
                    asyncio.create_task(mq.dispatch(chat_id, _handle()))
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
                            silent = (
                                os.environ.get("MINIAGENT_FEISHU_MEDIA_SILENT_REPLY", "")
                                .strip()
                                .lower()
                                in ("1", "true", "yes", "on")
                            )
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

                asyncio.create_task(mq.dispatch(chat_id, _handle_media()))
            elif msg_type == "post" and media_handler:
                post_items = _extract_post_media_items(content_str)
                if not post_items:
                    release_processing(message_id)
                    return
                thread_id_post = (message.thread_id or "").strip()

                async def _handle_post_media():
                    finalized = False
                    silent = (
                        os.environ.get("MINIAGENT_FEISHU_MEDIA_SILENT_REPLY", "")
                        .strip()
                        .lower()
                        in ("1", "true", "yes", "on")
                    )
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

                asyncio.create_task(mq.dispatch(chat_id, _handle_post_media()))
            else:
                release_processing(message_id)
                return

        except Exception as e:
            _logger.error("事件处理异常: %s", e)

    def _feishu_card_action_router_enabled() -> bool:
        """是否将卡片按钮事件经路由投递到消息队列（环境变量开关）。"""
        return (os.environ.get("MINIAGENT_FEISHU_CARD_ACTION_ROUTER") or "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )

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
            ev = getattr(event, "event", None)
            if not ev:
                return resp
            act = getattr(ev, "action", None)
            ctx = getattr(ev, "context", None)
            op = getattr(ev, "operator", None)
            value = dict(getattr(act, "value", None) or {}) if act else {}
            text = str(value.get("miniagent_text") or value.get("text") or "").strip()
            chat_id = str(value.get("chat_id") or "").strip()
            if not chat_id and ctx is not None:
                chat_id = str(getattr(ctx, "open_chat_id", None) or "").strip()
            sender_id = ""
            if op is not None:
                sender_id = str(getattr(op, "open_id", None) or "").strip()
            if not text or not chat_id:
                return resp
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
                loop = asyncio.get_running_loop()
            except RuntimeError:
                bad.content = "Mini Agent：无运行中的事件循环，无法调度"
                return resp

            loop.create_task(mq.dispatch(chat_id, _card_job()))
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
    _edb = EventDispatcherHandler.builder(encrypt_key, verification_token).register_p2_im_message_receive_v1(
        on_message_receive
    )
    if _feishu_card_action_router_enabled():
        _edb = _edb.register_p2_card_action_trigger(_on_card_action_trigger)
    event_handler = _edb.build()

    # 启动 WebSocket 客户端
    ws_client: Any = None
    ping_task: asyncio.Task[Any] | None = None
    try:
        # ── 关键修复：lark-oapi SDK 在模块加载时捕获了 event loop，
        #    但 asyncio.run() 会创建全新 loop。如果不替换，
        #    SDK 的 _receive_message_loop() 会调度到错误的 loop 上，
        #    导致消息永远收不到、思考回调永远不触发。
        import lark_oapi.ws.client as _sdk_ws_mod
        _sdk_ws_mod.loop = asyncio.get_running_loop()

        ws_client = lark.ws.Client(
            app_id=config.app_id,
            app_secret=config.app_secret,
            event_handler=event_handler,
            # 避免 SDK 在 stdout 输出与全屏 CLI 冲突（备用屏乱序 / 分层）
            log_level=LogLevel.ERROR,
        )

        _singleton_client = ws_client
        _singleton_app_id = config.app_id

        _logger.info("WebSocket 长轮询模式已启动（无需公网 IP）")
        _logger.info("消息会通过 WebSocket 自动从飞书服务器拉取")

        # lark-oapi 的 start() 是同步方法，内部调用 loop.run_until_complete()
        # 在已运行的事件循环中无法使用。直接调用内部异步方法：
        await ws_client._connect()

        ping_task = asyncio.create_task(ws_client._ping_loop())

        try:
            await asyncio.Event().wait()
        except (KeyboardInterrupt, asyncio.CancelledError):
            _logger.info("收到退出信号")
            await ws_client._disconnect()
            raise

    except Exception as e:
        _logger.error("WebSocket 启动失败: %s", e)
        raise
    finally:
        # 与 FeishuRuntime 循环开头的 reset 互补：保证异常/取消路径下 SDK 与单例状态一致。
        if ping_task is not None and not ping_task.done():
            ping_task.cancel()
            try:
                await ping_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        await reset_feishu_ws_singleton()


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
FEISHU_THINKING_PATCH_MIN_INTERVAL_S = 0.35
FEISHU_THINKING_PATCH_MIN_CHAR_DELTA = 450
FEISHU_THINKING_PATCH_BUDGET = 12


def feishu_card_body_max() -> int:
    """单张交互卡片 lark_md 正文上限（字符近似）；可用 MINI_AGENT_FEISHU_CARD_BODY_MAX 覆盖。"""
    raw = os.environ.get("MINI_AGENT_FEISHU_CARD_BODY_MAX", "").strip()
    if raw:
        try:
            return max(1000, int(raw))
        except ValueError:
            pass
    return 48_000


# 兼容旧导入：仅为进程首次 import 时的快照；运行时上限请以 feishu_card_body_max() 为准。
FEISHU_CARD_BODY_MAX = feishu_card_body_max()
FEISHU_THINKING_BODY_MAX = FEISHU_CARD_BODY_MAX


def _strip_unicode_replacement_chars(text: str) -> str:
    """去掉 U+FFFD，减少工具输出乱码时的占位符刷屏。"""
    return (text or "").replace("\ufffd", "")


def _neutralize_lone_asterisks_for_lark(text: str) -> str:
    """将不成对的 ASCII `*` 换成全角 `＊`，减轻 lark_md 把技术正文误解析为斜体。"""
    return re.sub(r"(?<!\*)\*(?!\*)", "\uff0a", text or "")


def _collapse_excessive_blank_lines(text: str) -> str:
    """将连续三个及以上换行压成双换行，避免卡片正文过长空白。"""
    return re.sub(r"\n{3,}", "\n\n", text or "")


_WIDE_TABLE_HINT = (
    ">（以下表格列数较多，飞书 lark_md 可能无法良好渲染；"
    "完整内容见本会话 **history.json** 或使用本地 CLI。）"
)


def _feishu_wide_table_fallback_mode() -> str:
    """``MINIAGENT_FEISHU_TABLE_FALLBACK``: hint / unicode / both（默认 both）。"""
    v = (os.environ.get("MINIAGENT_FEISHU_TABLE_FALLBACK") or "both").strip().lower()
    if v in ("hint", "unicode", "both"):
        return v
    return "both"


def _is_gfm_table_separator_line(line: str) -> bool:
    """判断一行是否为 GFM 表格分隔行（仅含 ``-``、``:``、``|`` 等）。"""
    return bool(re.match(r"^\s*\|?[\s\-:|]+\|?\s*$", line))


def _parse_gfm_table_row_cells(line: str) -> list[str]:
    """按管道符拆分表格行并 strip 各单元格（容忍首尾 ``|``）。"""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _gfm_table_block_to_text_table(
    block_lines: list[str], *, max_cell_width: int = 28
) -> str:
    """将 GFM 管道表转为等宽文本表（单元格截断），供 lark_md 代码块展示。"""
    rows: list[list[str]] = []
    for line in block_lines:
        if not line.strip() or _is_gfm_table_separator_line(line):
            continue
        rows.append(_parse_gfm_table_row_cells(line))
    if not rows:
        return ""
    ncols = max(len(r) for r in rows)
    for r in rows:
        while len(r) < ncols:
            r.append("")
    widths: list[int] = []
    for ci in range(ncols):
        mw = 0
        for r in rows:
            if ci < len(r):
                mw = max(mw, len((r[ci] or "").replace("\n", " ").replace("\r", "")))
        widths.append(min(max_cell_width, max(mw, 3)))

    def trunc(cell: str, w: int) -> str:
        """单元格截断/左对齐填充至宽度 ``w``。"""
        x = (cell or "").replace("\n", " ").replace("\r", "")
        if len(x) <= w:
            return x.ljust(w)
        if w <= 1:
            return "…"[:w]
        return x[: w - 1] + "…"

    out_lines: list[str] = []
    for ri, r in enumerate(rows):
        cells = [trunc(r[ci], widths[ci]) for ci in range(ncols)]
        out_lines.append("| " + " | ".join(cells) + " |")
        if ri == 0:
            out_lines.append(
                "|-" + "-|-".join("-" * widths[ci] for ci in range(ncols)) + "-|"
            )
    return "\n".join(out_lines)


def _normalize_lark_md(text: str) -> str:
    """将常见 GFM / HTML 写法降级为飞书 ``lark_md`` 更易接受的正文。"""
    if not text:
        return ""
    t = text.replace("\ufeff", "").replace("\u200b", "").replace("\u200c", "")
    t = _strip_unicode_replacement_chars(t)
    t = _neutralize_lone_asterisks_for_lark(t)
    t = re.sub(r"<br\s*/?>", "\n", t, flags=re.IGNORECASE)

    def _collapse_fence_line(line: str) -> str:
        """将过长反引号围栏起首统一为三个 ```，兼容飞书 lark_md。"""
        m = re.match(r"^(`{3,})(.*)$", line)
        if m and len(m.group(1)) > 3:
            return "```" + m.group(2)
        return line

    t = "\n".join(_collapse_fence_line(L) for L in t.split("\n"))

    # 过宽 Markdown 表格：列过多时整块替换为提示，避免客户端显示为「原始管道符」
    lines = t.split("\n")
    out: list[str] = []
    i = 0
    max_pipes = int(os.environ.get("MINIAGENT_FEISHU_LARK_TABLE_MAX_PIPES", "14"))
    while i < len(lines):
        row0 = lines[i]
        if i + 1 < len(lines) and "|" in row0 and re.match(
            r"^\s*\|?[\s\-:|]+\|?\s*$", lines[i + 1]
        ):
            j = i
            pipe_peak = 0
            while j < len(lines) and lines[j].strip() and "|" in lines[j]:
                pipe_peak = max(pipe_peak, lines[j].count("|"))
                j += 1
            if pipe_peak > max_pipes:
                mode = _feishu_wide_table_fallback_mode()
                utf_block = _gfm_table_block_to_text_table(lines[i:j])
                fenced = f"```\n{utf_block}\n```" if utf_block else ""
                if mode == "hint":
                    out.append(_WIDE_TABLE_HINT)
                elif mode == "unicode":
                    if fenced:
                        out.append(fenced)
                else:
                    out.append(
                        _WIDE_TABLE_HINT + (f"\n\n{fenced}" if fenced else "")
                    )
            else:
                out.extend(lines[i:j])
            i = j
            continue
        out.append(row0)
        i += 1
    joined = "\n".join(out)
    joined = re.sub(
        r"(?m)^[ \t]*(?:---+|\*{3,}|_{3,})[ \t]*$",
        "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500",
        joined,
    )
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
    return _prepare_thinking_body_for_card(raw, apply_cap=True)


def _feishu_reply_plain_enabled() -> bool:
    """``MINIAGENT_FEISHU_REPLY_PLAIN``：最终回复先发交互卡片但正文去掉常见 Markdown 标记（仍为 ``lark_md``）。"""
    v = os.environ.get("MINIAGENT_FEISHU_REPLY_PLAIN", "").strip().lower()
    return v in ("1", "true", "yes")


def _strip_light_markdown_for_feishu_plain(text: str) -> str:
    """弱化 Markdown 标记，减轻客户端对部分语法显示成「源码」时的观感（非完整解析器）。"""
    t = (text or "").replace("\r\n", "\n")
    t = re.sub(r"```[^\n]*\n([\s\S]*?)```", r"\1", t)
    t = re.sub(r"`([^`]+)`", r"\1", t)
    prev = None
    while prev != t:
        prev = t
        t = re.sub(r"\*\*([^*]+)\*\*", r"\1", t)
        t = re.sub(r"__([^_]+)__", r"\1", t)
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
    ``already_normalized=True`` 时跳过（用于已走 ``_prepare_thinking_body_for_card`` 的思考收尾；finalize 对各 chunk 用 ``_prepare_card_markdown(..., normalize=False)`` 仅做截断与 ``\\r``/``\\t`` 替换）。
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
    """交互卡片：正文为 lark_md（飞书客户端内渲染为 Markdown 子集）。"""
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": header_title},
            "template": template,
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": body_markdown}},
        ],
    }


def _thinking_interactive_card_dict(cleaned_markdown: str, template: str) -> dict[str, Any]:
    """构建标题为「思考中」的交互卡片 JSON dict（lark_md 正文）。"""
    return _feishu_interactive_card_dict("💭 思考中", cleaned_markdown, template)


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


def _patch_interactive_thinking_message(config: FeishuConfig, message_id: str, card_json: str) -> bool:
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
    except ImportError:
        pass
    except Exception as e:
        _logger.debug("更新思考消息异常: %s", e)
    return False


async def push_feishu_thinking_stream(
    config: FeishuConfig,
    chat_id: str,
    markdown: str,
    template: str,
    st: Any,
    *,
    new_round: bool,
) -> None:
    """ReAct 单轮 LLM 流式思考：同一会话只保留一条卡片，用 PATCH 节流更新（避免每条 chunk 新建消息）。"""
    import time

    chat_id = _normalize_im_receive_chat_id(chat_id)
    if not chat_id:
        return

    if new_round:
        st.feishu_thinking_message_id = None
        st.feishu_last_patch_monotonic = 0.0
        st.feishu_last_patched_char_len = -1
        st.feishu_patch_budget = FEISHU_THINKING_PATCH_BUDGET
        st.feishu_tool_section_started = False

    st.feishu_stream_accumulated = markdown
    cleaned = _prepare_thinking_markdown(markdown)
    card_json = json.dumps(_thinking_interactive_card_dict(cleaned, template), ensure_ascii=False)

    if not st.feishu_thinking_message_id:
        r_mid = getattr(st, "feishu_reply_to_message_id", None)
        r_thr = bool(getattr(st, "feishu_reply_in_thread", False))
        mid = _create_interactive_thinking_message(
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
        return

    now = time.monotonic()
    delta_t = now - st.feishu_last_patch_monotonic
    delta_c = len(markdown) - st.feishu_last_patched_char_len
    need_patch = delta_t >= FEISHU_THINKING_PATCH_MIN_INTERVAL_S or delta_c >= FEISHU_THINKING_PATCH_MIN_CHAR_DELTA
    if need_patch and st.feishu_patch_budget > 0:
        if _patch_interactive_thinking_message(config, st.feishu_thinking_message_id, card_json):
            st.feishu_patch_budget -= 1
            st.feishu_last_patch_monotonic = now
            st.feishu_last_patched_char_len = len(markdown)


async def finalize_feishu_thinking_stream(
    config: FeishuConfig,
    chat_id: str,
    template: str,
    st: Any,
) -> None:
    """一轮 LLM 流结束或非合并的非流式块前：PATCH 首张卡片为正文第一段；超长则追加多张「思考续页」卡片。"""
    chat_id = _normalize_im_receive_chat_id(chat_id)
    mid = getattr(st, "feishu_thinking_message_id", None)
    acc = getattr(st, "feishu_stream_accumulated", "") or ""
    if not chat_id or not mid or not acc.strip():
        return
    prep = _prepare_thinking_body_for_card(acc, apply_cap=False)
    chunks = _chunk_feishu_card_markdown(prep, already_normalized=True)
    if not chunks:
        return
    nch = len(chunks)
    first_body = _prepare_card_markdown(chunks[0], normalize=False)
    card_json = json.dumps(_thinking_interactive_card_dict(first_body, template), ensure_ascii=False)
    patched = _patch_interactive_thinking_message(config, mid, card_json)
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
            ok, _ = _post_interactive_message(
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
        st.feishu_thinking_message_id = None
        st.feishu_stream_accumulated = ""
        st.feishu_last_patched_char_len = -1
        st.feishu_tool_section_started = False


async def append_feishu_thinking_same_card(
    config: FeishuConfig,
    chat_id: str,
    tool_line: str,
    template: str,
    st: Any,
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
    cleaned = _prepare_thinking_markdown(acc2)
    card_json = json.dumps(_thinking_interactive_card_dict(cleaned, template), ensure_ascii=False)

    if mid:
        if not _patch_interactive_thinking_message(config, mid, card_json):
            _logger.warning(
                "飞书思考卡片追加工具后 PATCH 失败 message_id=%s（正文已累积，客户端可能未刷新）",
                mid,
            )
        return

    new_mid = _create_interactive_thinking_message(
        config,
        chat_id,
        card_json,
        reply_to_message_id=getattr(st, "feishu_reply_to_message_id", None),
        reply_in_thread=bool(getattr(st, "feishu_reply_in_thread", False)),
    )
    if new_mid:
        st.feishu_thinking_message_id = new_mid


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
        card_json = json.dumps(_thinking_interactive_card_dict(cleaned, template), ensure_ascii=False)
        ok, _ = _post_interactive_message(
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


def _send_interactive_reply_cards(
    config: FeishuConfig,
    cid: str,
    parts: list[str],
    *,
    reply_to_message_id: str | None = None,
    reply_in_thread: bool = False,
) -> tuple[int, int]:
    """发送多条交互卡片回复。返回 (已成功条数, 总条数)；任一分片失败即中止后续分片。"""
    n = len(parts)
    if n == 0:
        return (0, 0)
    sent = 0
    for i, part in enumerate(parts):
        body = _prepare_card_markdown(part)
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


