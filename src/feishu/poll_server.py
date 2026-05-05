"""Mini Agent Python — 飞书 WebSocket 长轮询服务器 (Phase 8)

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
from pathlib import Path
from typing import Any, Awaitable, Callable

from src.feishu.types import FeishuConfig

# 去重配置
DEDUP_TTL_MS = 5 * 60 * 1000  # 5 分钟
DEDUP_MAX_SIZE = 2000

# 单例状态
_singleton_client: Any = None
_singleton_app_id: str | None = None

# 内存去重
_processing_claims: dict[str, float] = {}

# 磁盘去重
_state_dir = os.path.join(
    os.environ.get("MINI_AGENT_STATE", os.getcwd()),
    ".mini-agent-state",
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


# ─── 顺序队列 ───

_chat_queues: dict[str, list] = {}


def enqueue_chat_message(chat_id: str, fn) -> None:
    """将消息加入聊天室顺序队列。"""
    queue = _chat_queues.get(chat_id)
    if queue is None:
        queue = []
        _chat_queues[chat_id] = queue
    queue.append(fn)

    # 如果队列长度为 1，说明没有正在处理，立即开始
    if len(queue) == 1:
        asyncio.create_task(_process_chat_queue(chat_id))


async def _process_chat_queue(chat_id: str) -> None:
    """处理聊天室顺序队列。"""
    queue = _chat_queues.get(chat_id)
    if not queue:
        return

    fn = queue.pop(0)
    try:
        await fn()
    except Exception as e:
        print(f"[飞书队列] 处理失败 [{chat_id}]: {e}")

    # 继续处理下一条
    if queue:
        asyncio.create_task(_process_chat_queue(chat_id))
    else:
        del _chat_queues[chat_id]


# ─── 消息防抖 ───

_debounce_timers: dict[str, asyncio.Task] = {}


def debounce_message(chat_id: str, fn, delay_ms: int = 1500) -> None:
    """对同一聊天室的消息进行防抖。

    1.5 秒内的连续消息，只处理最后一条。
    """
    existing = _debounce_timers.get(chat_id)
    if existing:
        existing.cancel()

    async def _delayed():
        await asyncio.sleep(delay_ms / 1000.0)
        _debounce_timers.pop(chat_id, None)
        try:
            await fn()
        except Exception as e:
            print(f"[飞书防抖] 处理失败: {e}")

    task = asyncio.create_task(_delayed())
    _debounce_timers[chat_id] = task


# ─── 飞书客户端 ───

async def start_feishu_poll_server(
    config: FeishuConfig,
    message_handler: Callable[[str, str, str], Awaitable[str]],
) -> None:
    """启动飞书 WebSocket 长轮询模式。

    建立与飞书服务器的 WebSocket 连接，
    持续接收事件推送并分发给消息处理器。

    Args:
        config: 飞书应用配置
        message_handler: 消息处理函数 (content, chatId, senderId) => reply
    """
    global _singleton_client, _singleton_app_id

    # 单客户端保护
    if _singleton_client and _singleton_app_id == config.app_id:
        print("[飞书] 已存在相同 appId 的 WSClient，复用现有连接")
        return

    if _singleton_client and _singleton_app_id != config.app_id:
        print(f"[飞书] 存在不同 appId 的 WSClient ({_singleton_app_id})，先关闭")
        await _singleton_client.close()
        _singleton_client = None
        _singleton_app_id = None

    # 创建 WSClient
    try:
        import lark_oapi as lark
        from lark_oapi.adapter.httpx import AsyncHttpClient
    except ImportError:
        print("❌ 请安装 lark-oapi: pip install lark-oapi")
        raise

    # 事件处理函数
    async def on_message_receive(data: Any):
        """处理 im.message.receive_v1 事件。"""
        try:
            message_id = data.get("message", {}).get("message_id", "")
            if not message_id:
                print("[飞书] 收到无 message_id 的事件，跳过")
                return

            # 去重检查
            if not try_begin_processing(message_id):
                print(f"[飞书去重] 跳过重复消息: {message_id}")
                return

            try:
                message = data.get("message", {})
                sender = data.get("sender", {})

                if not message:
                    release_processing(message_id)
                    return

                chat_id = message.get("chat_id", "")
                sender_id = sender.get("sender_id", {}).get("open_id", "")
                msg_type = message.get("message_type", "")

                if msg_type != "text":
                    release_processing(message_id)
                    return

                content_str = message.get("content", "")
                text = ""
                try:
                    parsed = json.loads(content_str)
                    text = parsed.get("text", "")
                except (json.JSONDecodeError, TypeError):
                    text = content_str

                if not text.strip():
                    release_processing(message_id)
                    return

                print(f"[飞书] 收到消息 [{chat_id}] {sender_id}: {text}")

                # 加入顺序队列 + 防抖
                async def _handle():
                    try:
                        reply = await message_handler(text, chat_id, sender_id)
                        if reply:
                            await _send_reply(config, chat_id, reply)
                            print(f"[飞书] 已回复 [{chat_id}]")
                    except Exception as e:
                        print(f"[飞书] 处理消息失败: {e}")
                    finally:
                        release_processing(message_id)

                enqueue_chat_message(chat_id, _handle)

            except Exception:
                release_processing(message_id)
                raise

        except Exception as e:
            print(f"[飞书] 事件处理异常: {e}")

    # 启动 WebSocket 客户端
    try:
        # 使用 lark-oapi WSClient
        ws_client = lark.ws.WSClient(
            app_id=config.app_id,
            app_secret=config.app_secret,
            event_handler=lark.ws.EventHandler(
                on_im_message_receive_v1=on_message_receive,
            ),
        )

        _singleton_client = ws_client
        _singleton_app_id = config.app_id

        print("🚀 飞书 WebSocket 长轮询模式已启动（无需公网 IP）")
        print("📌 消息会通过 WebSocket 自动从飞书服务器拉取")

        # 运行 WSClient（阻塞）
        await ws_client.start()

    except Exception as e:
        print(f"❌ 飞书 WebSocket 启动失败: {e}")
        raise


async def _send_reply(config: FeishuConfig, chat_id: str, reply: str) -> None:
    """通过飞书 API 发送回复。"""
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

        response = client.im.v1.message.create(request)
        if not response.success():
            print(f"[飞书] 发送回复失败: {response.code} {response.msg}")

    except ImportError:
        print("❌ 请安装 lark-oapi: pip install lark-oapi")
    except Exception as e:
        print(f"[飞书] 发送回复异常: {e}")


__all__ = ["start_feishu_poll_server", "try_begin_processing", "release_processing"]
