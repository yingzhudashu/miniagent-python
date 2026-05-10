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
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, Awaitable, Callable

from miniagent.feishu.types import FeishuConfig
from miniagent.infrastructure.logger import get_logger
_logger = get_logger(__name__)

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
            with open(_dedup_file, "r", encoding="utf-8") as f:
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


# 初始化磁盘去重
_load_disk_dedup()


# ─── 消息队列 ───
# 已由 miniagent.infrastructure.message_queue.MessageQueueManager 统一管理


# ─── 飞书客户端 ───

async def start_feishu_poll_server(
    config: FeishuConfig,
    message_handler: Callable[[str, str, str, str], Awaitable[str]],
    *,
    message_queue: Any,
) -> None:
    """启动飞书 WebSocket 长轮询模式。

    建立与飞书服务器的 WebSocket 连接，
    持续接收事件推送并分发给消息处理器。

    Args:
        config: 飞书应用配置
        message_handler: 消息处理函数 (content, chatId, senderId, chatType) => reply
        message_queue: 本进程使用的消息队列管理器（与 CLI 共用）
    """
    mq = message_queue
    global _singleton_client, _singleton_app_id

    # 单客户端保护
    if _singleton_client and _singleton_app_id == config.app_id:
        _logger.info("已存在相同 appId 的 WSClient，复用现有连接")
        return

    if _singleton_client and _singleton_app_id != config.app_id:
        _logger.info("存在不同 appId 的 WSClient (%s)，先关闭", _singleton_app_id)
        await _singleton_client.close()
        _singleton_client = None
        _singleton_app_id = None

    # 加载 SDK
    try:
        import lark_oapi as lark
        from lark_oapi.api.im.v1.model.p2_im_message_receive_v1 import P2ImMessageReceiveV1
        from lark_oapi.event.dispatcher_handler import EventDispatcherHandler
        from lark_oapi.core.enum import LogLevel
    except ImportError as e:
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

            if msg_type != "text":
                release_processing(message_id)
                return

            content_str = message.content or ""
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

            # 调度异步处理
            async def _handle():
                try:
                    reply = await message_handler(text, chat_id, sender_id, chat_type)
                    if reply:
                        await _send_reply(config, chat_id, reply)
                        _logger.debug("已回复 [%s]", chat_id)
                except Exception as e:
                    _logger.error("处理消息失败: %s", e)
                finally:
                    release_processing(message_id)

            # 按 chat_id 入队：与同聊天室消息串行，避免多协程交错改写上下文
            asyncio.create_task(mq.dispatch(chat_id, _handle()))

        except Exception as e:
            _logger.error("事件处理异常: %s", e)

    # 构建 EventDispatcherHandler
    encrypt_key = config.encrypt_key or ""
    verification_token = config.verification_token or ""
    event_handler = (
        EventDispatcherHandler.builder(
            encrypt_key, verification_token
        )
        .register_p2_im_message_receive_v1(on_message_receive)
        .build()
    )

    # 启动 WebSocket 客户端
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

        # 启动 ping 循环
        asyncio.create_task(ws_client._ping_loop())

        # 等待连接断开
        try:
            await asyncio.Event().wait()
        except (KeyboardInterrupt, asyncio.CancelledError):
            _logger.info("收到退出信号")
            await ws_client._disconnect()

    except Exception as e:
        _logger.error("WebSocket 启动失败: %s", e)
        raise


async def _send_reply(config: FeishuConfig, chat_id: str, reply: str) -> None:
    """通过飞书 API 发送回复（使用交互式卡片）。

    Args:
        config: 飞书应用配置
        chat_id: 飞书聊天室 ID（如 oc_xxx）
        reply: 回复文本
    """
    # 验证 chat_id 有效性
    if not chat_id or not chat_id.startswith("oc_"):
        _logger.debug("跳过发送回复：无效的 chat_id (%s)", chat_id)
        return

    try:
        import lark_oapi as lark
        from lark_oapi.api.im.v1 import (
            CreateMessageRequest,
            CreateMessageRequestBody,
        )

        client = lark.Client.builder().app_id(config.app_id).app_secret(config.app_secret).build()

        # 构建飞书交互式卡片
        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": "🤖 Mini Agent"},
                "template": "blue"
            },
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": reply}}
            ]
        }

        request = CreateMessageRequest.builder() \
            .receive_id_type("chat_id") \
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("interactive")
                .content(json.dumps(card))
                .build()
            ) \
            .build()

        response = client.im.v1.message.create(request)
        if not response.success():
            _logger.warning("发送回复失败: %s %s", response.code, response.msg)

    except ImportError:
        _logger.error("请安装 lark-oapi: pip install lark-oapi")
    except Exception as e:
        _logger.error("发送回复异常: %s", e)
        # 降级为纯文本
        try:
            import lark_oapi as lark
            from lark_oapi.api.im.v1 import (
                CreateMessageRequest,
                CreateMessageRequestBody,
            )
            client = lark.Client.builder().app_id(config.app_id).app_secret(config.app_secret).build()
            request = CreateMessageRequest.builder() \
                .receive_id_type("chat_id") \
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(chat_id)
                    .msg_type("text")
                    .content(json.dumps({"text": reply}))
                    .build()
                ) \
                .build()
            client.im.v1.message.create(request)
        except Exception:
            pass


def _normalize_im_receive_chat_id(chat_id: str) -> str:
    """去掉内部路由前缀 ``feishu:``，得到 IM API 可用的 ``receive_id``（``receive_id_type=chat_id``）。"""
    c = (chat_id or "").strip()
    if c.startswith("feishu:"):
        return c[len("feishu:") :]
    return c


# 单条「思考」卡片：流式时用 PATCH 更新同一 message_id；飞书对单条消息可 PATCH 次数有限，须节流。
FEISHU_THINKING_BODY_MAX = 12000
FEISHU_THINKING_PATCH_MIN_INTERVAL_S = 0.35
FEISHU_THINKING_PATCH_MIN_CHAR_DELTA = 450
FEISHU_THINKING_PATCH_BUDGET = 12


def _thinking_interactive_card_dict(cleaned_markdown: str, template: str) -> dict[str, Any]:
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "💭 思考中"},
            "template": template,
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": cleaned_markdown}},
        ],
    }


def _prepare_thinking_markdown(raw: str) -> str:
    t = raw if len(raw) <= FEISHU_THINKING_BODY_MAX else raw[: FEISHU_THINKING_BODY_MAX] + "…"
    return t.replace("\r", "").replace("\t", "  ")


def _create_interactive_thinking_message(
    config: FeishuConfig, chat_id: str, card_json: str
) -> str | None:
    """创建交互式思考卡片，成功返回 message_id。"""
    try:
        import lark_oapi as lark
        from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

        client = lark.Client.builder().app_id(config.app_id).app_secret(config.app_secret).build()
        request = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("interactive")
                .content(card_json)
                .build()
            )
            .build()
        )
        response = client.im.v1.message.create(request)
        if response.success() and response.data and response.data.message_id:
            return response.data.message_id
        _logger.warning("创建思考消息失败: %s %s", response.code, response.msg)
    except ImportError:
        _logger.error("请安装 lark-oapi: pip install lark-oapi")
    except Exception as e:
        _logger.debug("创建思考消息异常: %s", e)
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

    st.feishu_stream_accumulated = markdown
    cleaned = _prepare_thinking_markdown(markdown)
    card_json = json.dumps(_thinking_interactive_card_dict(cleaned, template), ensure_ascii=False)

    if not st.feishu_thinking_message_id:
        mid = _create_interactive_thinking_message(config, chat_id, card_json)
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
    """一轮 LLM 流结束或进入工具行前：PATCH 为最终全文，并释放 message_id（下一轮新建卡片）。"""
    chat_id = _normalize_im_receive_chat_id(chat_id)
    mid = getattr(st, "feishu_thinking_message_id", None)
    acc = getattr(st, "feishu_stream_accumulated", "") or ""
    if not chat_id or not mid or not acc.strip():
        return
    cleaned = _prepare_thinking_markdown(acc)
    card_json = json.dumps(_thinking_interactive_card_dict(cleaned, template), ensure_ascii=False)
    if _patch_interactive_thinking_message(config, mid, card_json):
        st.feishu_thinking_message_id = None
        st.feishu_stream_accumulated = ""
        st.feishu_last_patched_char_len = -1


async def _send_thinking(config: FeishuConfig, chat_id: str, thinking: str, template: str = "gray") -> None:
    """通过飞书 API 发送思考过程（使用交互式卡片）。

    Args:
        config: 飞书应用配置
        chat_id: 飞书聊天室 ID（如 oc_xxx）；勿传入 ``feishu:oc_xxx`` 形式（会自动剥离前缀）
        thinking: 思考过程文本
        template: 卡片颜色模板（gray=思考中, blue=已回复）
    """
    chat_id = _normalize_im_receive_chat_id(chat_id)
    if not chat_id:
        _logger.debug("跳过发送思考：空的 chat_id")
        return

    try:
        import lark_oapi as lark
        from lark_oapi.api.im.v1 import (
            CreateMessageRequest,
            CreateMessageRequestBody,
        )

        client = lark.Client.builder().app_id(config.app_id).app_secret(config.app_secret).build()

        # 工具意图等短行：单独一条卡片（非流式轮次）
        short = thinking[:2000] + "…" if len(thinking) > 2000 else thinking
        cleaned = _prepare_thinking_markdown(short)
        card_json = json.dumps(_thinking_interactive_card_dict(cleaned, template), ensure_ascii=False)

        request = CreateMessageRequest.builder() \
            .receive_id_type("chat_id") \
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("interactive")
                .content(card_json)
                .build()
            ) \
            .build()

        response = client.im.v1.message.create(request)
        if not response.success():
            _logger.warning("发送思考失败: %s %s", response.code, response.msg)

    except ImportError:
        pass  # 静默失败，不影响主流程
    except Exception as e:
        _logger.debug("发送思考异常: %s", e)



