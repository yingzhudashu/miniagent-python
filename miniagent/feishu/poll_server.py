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
from miniagent.infrastructure.env_parse import TRUTHY, env_flag, env_flag_strict, env_str
from miniagent.infrastructure.json_config import get_config
from miniagent.infrastructure.logger import get_logger

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

# 引擎引用（供确认侧通道使用，卡片按钮回调中直接响应）
_feishu_confirmation_engine: Any | None = None


def set_feishu_confirmation_engine(engine: Any) -> None:
    """设置飞书侧可访问的引擎引用，供卡片确认按钮使用。"""
    global _feishu_confirmation_engine
    _feishu_confirmation_engine = engine
    cc = getattr(engine, "confirmation_channel", None) if engine else None
    _logger.info(
        "set_feishu_confirmation_engine: engine=%s, confirmation_channel=%s",
        engine is not None,
        cc is not None,
    )


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
    mode = env_str("MINIAGENT_FEISHU_REPLY_TARGET", "reply").lower()
    if mode != "reply":
        return None, False
    mid = (trigger_message_id or "").strip()
    if not mid:
        return None, False
    raw_thr = env_str("MINIAGENT_FEISHU_REPLY_IN_THREAD").lower()
    if raw_thr in ("0", "false", "no", "off"):
        thr = False
    elif raw_thr in TRUTHY:
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
                    out.append(("image", str(ik), "image"))
            elif tag == "media":
                fk = node.get("file_key")
                if fk and ("file", str(fk)) not in seen:
                    seen.add(("file", str(fk)))
                    nm = node.get("file_name") or node.get("name") or "download"
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
    global _singleton_client, _singleton_app_id, _ws_shutdown_event
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
_ws_shutdown_event: asyncio.Event | None = None


def request_feishu_ws_shutdown() -> None:
    """请求结束当前 WebSocket 会话监督（供 ``stop_async`` / 进程 shutdown 调用）。"""
    ev = _ws_shutdown_event
    if ev is not None:
        ev.set()


# 内存去重
_processing_claims: dict[str, float] = {}

# 磁盘去重
_state_dir = os.path.join(
    get_config("paths.state_dir", os.path.join(os.getcwd(), "workspaces")),
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
    return True


def release_processing(message_id: str) -> None:
    """释放处理权并记录到磁盘去重。

    从内存处理权映射中移除该消息 ID，同时写入磁盘去重映射并
    执行容量裁剪（超过 DEDUP_MAX_SIZE 时删除最老的 20%）。

    Args:
        message_id: 飞书消息 ID
    """
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
            _logger.warning("检测到残留 WebSocket 单例（与当前 appId 相同），将关闭后重建")
        await reset_feishu_ws_singleton()

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
        except Exception:
            pass
        # #endregion
        _logger.error("请安装 lark-oapi: pip install lark-oapi (%s)", e)
        raise

    from miniagent.feishu.ws_health import touch_ws_inbound_activity

    # 同步回调（SDK 要求 sync），内部通过 asyncio.create_task 调度 async 逻辑
    def on_message_receive(event: P2ImMessageReceiveV1) -> None:
        """处理 im.message.receive_v1 事件。"""
        try:
            touch_ws_inbound_activity()
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
                        message_id, _msg_age, _max_age,
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
                if msg_type == "interactive" and env_flag(
                    "MINIAGENT_FEISHU_CARD_EXTRACT_INBOUND", default=True
                ):
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
                    # 需求澄清追问拦截：普通消息自动注入为回答
                    _cc_eng = _feishu_confirmation_engine
                    _cc = getattr(_cc_eng, "confirmation_channel", None) if _cc_eng else None
                    if _cc and _cc.has_pending:
                        from miniagent.types.confirmation import (
                            ConfirmationResult,
                            ConfirmationStage,
                        )

                        if _cc.pending.stage == ConfirmationStage.CLARIFICATION:
                            _logger.info("飞书澄清拦截: chat_id=%s, text=%s", chat_id[:12], text[:60])
                            _cc.respond(ConfirmationResult(approved=True, adjustment=text))
                            release_processing(message_id)
                            _logger.info("飞书澄清已响应: confirmation_channel.respond() 已调用")
                        else:
                            _logger.debug(
                                "飞书拦截: 有待确认请求但阶段为 %s，非 CLARIFICATION，走消息队列",
                                getattr(_cc.pending.stage, "value", _cc.pending.stage),
                            )
                            asyncio.create_task(mq.dispatch(chat_id, _handle()))
                    else:
                        # _cc.has_pending 已为 False，无需重复打印
                        _logger.debug("飞书拦截: 无待确认请求，走消息队列")
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
                            silent = env_flag("MINIAGENT_FEISHU_MEDIA_SILENT_REPLY", default=False)
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
                    silent = env_flag("MINIAGENT_FEISHU_MEDIA_SILENT_REPLY", default=False)
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
        return env_flag_strict("MINIAGENT_FEISHU_CARD_ACTION_ROUTER", default=True)

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
            touch_ws_inbound_activity()
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
                from miniagent.feishu.cards.dedupe import should_skip_card_action

                if should_skip_card_action(dedupe_key):
                    ok = CallBackToast()
                    ok.type = "info"
                    ok.content = "已处理（重复操作已忽略）"
                    resp.toast = ok
                    return resp
            if not text or not chat_id:
                return resp

            # 拦截确认命令：直接响应确认通道，不经消息队列
            if text in (".confirm", ".reject") or text.startswith(".adjust "):
                engine = _feishu_confirmation_engine
                if engine is not None:
                    cc = getattr(engine, "confirmation_channel", None)
                    if cc is not None and cc.has_pending:
                        from miniagent.types.confirmation import ConfirmationResult

                        if text == ".confirm":
                            cc.respond(ConfirmationResult(approved=True))
                            ok = CallBackToast()
                            ok.type = "success"
                            ok.content = "✅ 已确认，继续执行"
                            resp.toast = ok
                            return resp
                        elif text == ".reject":
                            cc.respond(ConfirmationResult(approved=False, rejected=True))
                            ok = CallBackToast()
                            ok.type = "warning"
                            ok.content = "⚠️ 已拒绝，取消当前操作"
                            resp.toast = ok
                            return resp
                        else:
                            adjustment = text[len(".adjust "):].strip()
                            if adjustment:
                                cc.respond(ConfirmationResult(approved=True, adjustment=adjustment))
                                ok = CallBackToast()
                                ok.type = "success"
                                ok.content = f"✅ 已调整：{adjustment[:40]}{'…' if len(adjustment) > 40 else ''}"
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
    _edb = EventDispatcherHandler.builder(
        encrypt_key, verification_token
    ).register_p2_im_message_receive_v1(on_message_receive)
    if _feishu_card_action_router_enabled():
        _edb = _edb.register_p2_card_action_trigger(_on_card_action_trigger)
    event_handler = _edb.build()

    # 启动 WebSocket 客户端
    from miniagent.feishu.ws_client import FeishuWsClient
    from miniagent.feishu.ws_health import get_last_ws_session_end, supervise_feishu_ws_session

    global _ws_shutdown_event

    ws_client: FeishuWsClient | None = None
    ping_task: asyncio.Task[Any] | None = None
    shutdown_event = asyncio.Event()
    _ws_shutdown_event = shutdown_event
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

        _singleton_client = ws_client
        _singleton_app_id = config.app_id

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
            )
            end_reason, _ = get_last_ws_session_end()
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
        _ws_shutdown_event = None
        # 与 FeishuRuntime 循环开头的 reset 互补：保证异常/取消路径下 SDK 与单例状态一致。
        if ping_task is not None and not ping_task.done():
            ping_task.cancel()
            try:
                await ping_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
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
                except (asyncio.CancelledError, ConnectionClosedOK):
                    pass
                except Exception:
                    pass
            elif recv_task is not None:
                # 已完成的任务显式读取结果，清除未检索异常
                try:
                    recv_task.result()
                except (ConnectionClosedOK, Exception):
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
# 节流参数从JSON配置读取，默认值已优化为更流畅的流式体验（间隔更短、字符增量更小）

# 默认值：间隔 0.12s（比之前 0.35s 更快）、字符增量 30（比之前 450 更小）、预算 40（比之前 12 更多）
FEISHU_THINKING_PATCH_MIN_INTERVAL_S = float(get_config("feishu.patch.interval", 0.12))
FEISHU_THINKING_PATCH_MIN_CHAR_DELTA = int(get_config("feishu.patch.char_delta", 30))
FEISHU_THINKING_PATCH_BUDGET = int(get_config("feishu.patch.budget", 40))


def feishu_card_body_max() -> int:
    """单张交互卡片 lark_md 正文上限（字符近似）。"""
    val = get_config("feishu.card.body_max_chars", 48000)
    return max(1000, int(val)) if val else 48_000


# 兼容旧导入：仅为进程首次 import 时的快照；运行时上限请以 feishu_card_body_max() 为准。
FEISHU_CARD_BODY_MAX = feishu_card_body_max()


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
    return _prepare_thinking_body_for_card(raw, apply_cap=True)


def _feishu_reply_plain_enabled() -> bool:
    """``MINIAGENT_FEISHU_REPLY_PLAIN``：默认渲染富文本 Markdown；设为 ``1`` 时去掉常见 Markdown 标记（仍为 ``lark_md``）。"""
    return env_flag_strict("MINIAGENT_FEISHU_REPLY_PLAIN", default=False)


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


def _thinking_interactive_card_dict(cleaned_markdown: str, template: str) -> dict[str, Any]:
    """构造思考内容交互卡片（可能包含确认按钮）。"""
    from miniagent.feishu.cards.builder import confirmation_buttons, thinking_card_dict

    # 检查是否有待确认请求，有则附加按钮
    buttons = None
    cc = _feishu_confirmation_engine
    if cc is not None:
        cc_obj = getattr(cc, "confirmation_channel", None)
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

    # 工具段提取：仅 new_round=True 且旧轮有工具时需要保留旧轮 LLM 正文（不含工具段）。
    _TOOL_MARKER = "\n\n**工具**"
    existing = getattr(st, "feishu_stream_accumulated", "") or ""
    tool_section = ""
    if _TOOL_MARKER in existing and getattr(st, "feishu_tool_section_started", False):
        tool_section = existing[existing.index(_TOOL_MARKER):]

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
    else:
        _round_separator = False

    if _round_separator:
        # 新轮已写入分隔符（仅含旧轮 LLM 正文），追加新轮 LLM 正文并重新附上工具段。
        st.feishu_stream_accumulated += (markdown or "")
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

    cleaned = _prepare_thinking_markdown(st.feishu_stream_accumulated)
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
    need_patch = (
        delta_t >= FEISHU_THINKING_PATCH_MIN_INTERVAL_S
        or delta_c >= FEISHU_THINKING_PATCH_MIN_CHAR_DELTA
    )
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
    if not chat_id or not mid:
        # 无卡片可 finalize，仍清理状态
        st.feishu_thinking_message_id = None
        st.feishu_stream_accumulated = ""
        st.feishu_last_patched_char_len = -1
        st.feishu_tool_section_started = False
        st.feishu_pending_tool_lines = []
        st.feishu_stream_llm_len = 0
        return
    if not acc.strip():
        # 无累积内容，直接清理状态
        st.feishu_thinking_message_id = None
        st.feishu_stream_accumulated = ""
        st.feishu_last_patched_char_len = -1
        st.feishu_tool_section_started = False
        st.feishu_pending_tool_lines = []
        st.feishu_stream_llm_len = 0
        return
    prep = _prepare_thinking_body_for_card(acc, apply_cap=False)
    chunks = _chunk_feishu_card_markdown(prep, already_normalized=True)
    if not chunks:
        return
    nch = len(chunks)
    first_body = _prepare_card_markdown(chunks[0], normalize=False)
    card_json = json.dumps(
        _thinking_interactive_card_dict(first_body, template), ensure_ascii=False,
    )
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
        st.feishu_pending_tool_lines = []
        st.feishu_stream_llm_len = 0


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
    suggestions = getattr(reflection, "suggestions", []) or []

    lines: list[str] = [
        "### 质量评估结果",
        f"- **状态**：{status}",
        f"- **评分**：{score:.1f}/1.0",
    ]
    if suggestions:
        lines.append("")
        lines.append("### 改进建议")
        for s in suggestions[:5]:
            lines.append(f"- {s}")

    body = "\n".join(lines)
    cleaned = _prepare_thinking_markdown(body)
    # 使用 "🤖 Mini Agent" 卡片头，与 .help 命令输出格式一致
    card_json = json.dumps(_feishu_interactive_card_dict("🤖 Mini Agent", cleaned, template), ensure_ascii=False)

    ok, _ = _post_interactive_message(
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


__all__ = [
    "set_feishu_confirmation_engine",
    "feishu_outbound_reply_params",
    "FeishuMediaHandler",
    "reset_feishu_ws_singleton",
    "request_feishu_ws_shutdown",
    "try_begin_processing",
    "release_processing",
    "abandon_processing_claim",
    "start_feishu_poll_server",
    "feishu_card_body_max",
    "push_feishu_thinking_stream",
    "finalize_feishu_thinking_stream",
    "append_feishu_thinking_same_card",
    "send_reflection_card",
]
